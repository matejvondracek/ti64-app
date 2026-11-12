"""MATLAB backend: MATLAB R2025b + MTEX 7.2 through the MATLAB Engine (`matlabengine==25.2.*`).

- One persistent engine per Python process, started lazily on first use (startup ~10-20 s). It is always a NEW
  MATLAB process without the desktop (-nodesktop -nosplash); the app never attaches to the user's open MATLAB
  (no connect_matlab / find_matlab / shared sessions), so a crash of the desktop (e.g. its clipboard service,
  docs/decisions.md D23) cannot take the app down and the app cannot disturb the user's session.
- Logs: the engine's command-window output goes to %APPDATA%/ti64app/logs/matlab-engine-<python pid>.log
  (-logfile), MATLAB crash dumps to the same folder (MATLAB_LOG_DIR).
- Every call runs in the background and is watched: if the engine raises an EngineError with the MATLAB process
  gone, the process disappears, or a crash dump of the engine's process appears (MATLAB may keep a crashed process
  alive), the process is killed, a new engine is started once and EngineRestarted is raised with a plain-language
  message. Objects of the old session are gone; handles() then lists only the new session's objects, so the service
  layer rebuilds what a rerun needs (service.materialize). A start longer than START_TIMEOUT_S is aborted.
- MTEX objects stay in the MATLAB session (ti64.store) and are referenced by ID (random, never reused).
- Per-pixel arrays move to Python through temporary HDF5 files (ti64.exportEbsd + h5py),
  never element by element through the engine.
- If MATLAB, the engine package or MTEX is missing, the backend reports itself as unavailable.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from .. import __version__
from ..settings import REPO_ROOT, load_settings, logs_dir
from .base import (BackendUnavailable, ChartSpec, CleanupParams, ColonyParams, CleanupReport, EBSDData, EBSDHandle,
                   EngineRestarted, GrainParams, GrainsData, GrainsHandle, LoadParams, MapParams, ReconData,
                   RenderSpec)

MATLAB_DIR = REPO_ROOT / "matlab"
START_OPTIONS = "-nodesktop -nosplash"
START_TIMEOUT_S = float(os.environ.get("TI64_MATLAB_START_TIMEOUT", "240"))
WATCH_S = 2.0                # how often the MATLAB process / crash dumps are checked during a call
log = logging.getLogger("ti64app.matlab")


class _EngineDied(Exception):
    """Internal: the MATLAB process of the engine is gone or crashed."""

    def __init__(self, reason: str, crash_dump: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.crash_dump = crash_dump


def _create_time(pid: int) -> float:
    """Start time of a process (0 if it does not exist): a pid alone can be reused by another process."""
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        return 0.0


class MatlabBackend:
    name = "matlab"

    def __init__(self) -> None:
        self._eng = None
        self._pid: int | None = None          # MATLAB process of the current engine
        self._pid_started = 0.0
        self._t_start = 0.0
        self._lock = threading.RLock()
        self._log = io.StringIO()
        self._futures: set = set()            # running calls (cancel_running)
        self.log_file: Path | None = None     # MATLAB -logfile of the current engine
        self.generation = 0                   # +1 per engine start (0 = never started)
        self.restarts = 0                     # restarts after a crash in this Python process
        self.notice: str | None = None        # last restart message (GUI: show once via pop_notice)

    # -- availability -------------------------------------------------------------------------
    def is_available(self) -> bool:
        return self.unavailable_reason() == ""

    def unavailable_reason(self) -> str:
        try:
            if importlib.util.find_spec("matlab") is None or importlib.util.find_spec("matlab.engine") is None:
                return "MATLAB Engine for Python (matlabengine==25.2.*) is not installed"
        except (ImportError, ValueError):
            return "MATLAB Engine for Python could not be imported"
        mtex = load_settings().paths.mtex_path
        if not mtex:
            return "paths.mtex_path is not set (settings.local.toml or %APPDATA%/ti64app/settings.toml)"
        if not (Path(mtex) / "startup_mtex.m").is_file():
            return f"no startup_mtex.m in paths.mtex_path = {mtex}"
        return ""

    # -- engine -------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._eng is not None

    def engine(self):
        """The persistent engine (started once) with matlab/ and MTEX on the path."""
        with self._lock:
            if self._eng is None:
                reason = self.unavailable_reason()
                if reason:
                    raise BackendUnavailable(reason)
                self._start()
            return self._eng

    @staticmethod
    def _engine_module():
        import matlab.engine  # noqa: PLC0415  (optional dependency)

        return matlab.engine

    def _start(self) -> None:
        """Start a NEW MATLAB process without the desktop (never attach to an open MATLAB), within a time limit."""
        from ..logs import prune  # noqa: PLC0415

        me = self._engine_module()
        d = logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.log_file = d / f"matlab-engine-{os.getpid()}.log"
        prune("matlab-engine-*.log", keep=10)
        prune("matlab_crash_dump*", keep=10)
        os.environ["MATLAB_LOG_DIR"] = str(d)          # crash dumps of the engine go next to its log
        opts = f'{START_OPTIONS} -logfile "{self.log_file}"'
        log.info("starting MATLAB engine: %s", opts)
        t0 = time.time()
        fut = me.start_matlab(opts, background=True)
        try:
            eng = fut.result(timeout=START_TIMEOUT_S)
        except me.TimeoutError as e:
            try:
                fut.cancel()
            except Exception:  # noqa: BLE001
                pass
            raise BackendUnavailable(f"MATLAB did not start within {START_TIMEOUT_S:.0f} s (log: {self.log_file}). "
                                     "Check the MATLAB licence / installation, or choose the pymtex backend in "
                                     "Settings.") from e
        except Exception as e:  # noqa: BLE001
            raise BackendUnavailable(f"MATLAB could not be started: {e} (log: {self.log_file})") from e
        self._eng = eng
        try:
            self._pid = int(eng.feature("getpid"))
            self._pid_started = _create_time(self._pid)
            eng.addpath(str(MATLAB_DIR), nargout=0)
            eng.ti64.setupMtex(load_settings().paths.mtex_path, nargout=0, stdout=self._log)
        except Exception as e:  # noqa: BLE001
            self._forget_engine(kill=True)
            raise BackendUnavailable(f"MATLAB started but MTEX could not be set up: {e} (log: {self.log_file})") from e
        self._t_start = t0
        self.generation += 1
        log.info("MATLAB engine %d started in %.1f s (pid %s)", self.generation, time.time() - t0, self._pid)

    def call(self, fn: str, *args, nargout: int = 1):
        """Call a MATLAB function by name; MTEX console output goes to self.log. If MATLAB dies during the call,
        a new engine is started and EngineRestarted is raised (plain-language message; run the step again)."""
        me = self._engine_module()
        eng = self.engine()
        gen = self.generation
        try:
            fut = eng.feval(fn, *args, nargout=nargout, stdout=self._log, stderr=self._log, background=True)
            return self._wait(fut)
        except _EngineDied as e:
            self._recover(gen, fn, e.reason, e.crash_dump)
        except (me.EngineError, me.RejectedExecutionError, SystemError) as e:
            if not self._dead_soon():
                raise                          # an engine error while MATLAB is still running
            self._recover(gen, fn, "the MATLAB process ended", self._crash_dump())
        except me.CancelledError:
            raise InterruptedError(f"MATLAB call {fn} was cancelled") from None

    def _wait(self, fut):
        """Result of a background call; watches the MATLAB process while waiting (never hangs on a dead engine)."""
        me = self._engine_module()
        self._futures.add(fut)
        try:
            while True:
                try:
                    return fut.result(timeout=WATCH_S)     # returns as soon as the call is done
                except me.TimeoutError:
                    pass
                dump = self._crash_dump()
                if dump:
                    raise _EngineDied("MATLAB crashed", dump)
                if not self._alive():
                    raise _EngineDied("the MATLAB process ended")
        finally:
            self._futures.discard(fut)

    def cancel_running(self) -> int:
        """Ask MATLAB to cancel the calls that are running (e.g. Cancel in the GUI). Returns how many."""
        n = 0
        for f in list(self._futures):
            try:
                n += bool(f.cancel())
            except Exception:  # noqa: BLE001
                pass
        return n

    def _alive(self) -> bool:
        if self._pid is None:
            return self._eng is not None
        return self._pid_started > 0 and _create_time(self._pid) == self._pid_started

    def _dead_soon(self, wait_s: float = 5.0) -> bool:
        """After an EngineError: is the MATLAB process gone (or crashed)? A killed process can still be listed for a
        moment, so this waits up to `wait_s`."""
        t_end = time.monotonic() + wait_s
        while True:
            if not self._alive() or self._crash_dump():
                return True
            if time.monotonic() >= t_end:
                return False
            time.sleep(0.1)

    def _crash_dump(self) -> str | None:
        """A MATLAB crash dump of the engine's process written since it started (logs folder or %TEMP%)."""
        if self._pid is None:
            return None
        for folder in {logs_dir(), Path(tempfile.gettempdir())}:
            for f in folder.glob(f"matlab_crash_dump.{self._pid}-*"):
                try:
                    if f.stat().st_mtime >= self._t_start - 1:
                        return str(f)
                except OSError:
                    pass
        return None

    def _forget_engine(self, kill: bool) -> None:
        """Drop the current engine; kill its MATLAB process if it is still there (crashed but alive)."""
        if kill and self._pid is not None and self._alive():
            try:
                import psutil  # noqa: PLC0415

                psutil.Process(self._pid).kill()
            except Exception:  # noqa: BLE001
                pass
        eng, self._eng, self._pid, self._pid_started = self._eng, None, None, 0.0
        self._futures.clear()
        if eng is not None:                      # release the dead session now (else __del__ raises at exit)
            try:
                eng.exit()
            except Exception:  # noqa: BLE001  ("MATLAB process cannot be terminated": it is gone)
                getattr(eng, "__dict__", {}).pop("_matlab", None)
        try:                                     # matlab.engine would try to exit the dead engine at interpreter exit
            me = self._engine_module()
            me._engines[:] = [w for w in me._engines if w() is not eng]
        except Exception:  # noqa: BLE001
            pass

    def _recover(self, gen: int, fn: str, reason: str, crash_dump: str | None) -> None:
        """MATLAB died during `fn`: kill what is left, start a new engine once, raise EngineRestarted."""
        with self._lock:
            if crash_dump and Path(crash_dump).parent != logs_dir():
                try:
                    crash_dump = str(shutil.copy2(crash_dump, logs_dir() / Path(crash_dump).name))
                except OSError:
                    pass
            old_log = self.log_file
            log.error("MATLAB engine %d died during %s: %s (log %s, crash dump %s)", gen, fn, reason, old_log,
                      crash_dump)
            if self.generation == gen:           # not yet restarted by another thread
                self._forget_engine(kill=True)
                self.restarts += 1
                try:
                    self._start()
                except BackendUnavailable as e:
                    self.notice = f"MATLAB stopped unexpectedly ({reason}) and could not be restarted: {e}"
                    raise BackendUnavailable(self.notice) from None
            where = f"Crash report: {crash_dump}" if crash_dump else f"MATLAB log: {old_log}"
            self.notice = (f"MATLAB stopped unexpectedly ({reason}) and was restarted. Maps kept in MATLAB were "
                           f"lost; they are reloaded automatically when you run the step again. {where}")
            raise EngineRestarted(self.notice, log_file=str(old_log) if old_log else None, crash_dump=crash_dump)

    def pop_notice(self) -> str | None:
        """The last restart message, once (for a GUI notification)."""
        n, self.notice = self.notice, None
        return n

    @property
    def log(self) -> str:
        return self._log.getvalue()

    def pid(self) -> int:
        return int(self.call("feature", "getpid"))

    def close(self) -> None:
        with self._lock:
            if self._eng is not None:
                try:
                    if self._alive():
                        self._eng.quit()
                except Exception:  # noqa: BLE001  (already dead)
                    pass
                finally:
                    self._forget_engine(kill=False)

    def versions(self) -> dict[str, str]:
        return {"app": __version__, "matlab": str(self.call("version")),
                "mtex": str(self.call("getMTEXpref", "version"))}

    # -- objects ------------------------------------------------------------------------------
    def load_ebsd(self, params: LoadParams) -> EBSDHandle:
        import matlab  # noqa: PLC0415

        ids = sorted(params.phase_names)
        hid, n = self.call("ti64.loadEbsd", str(params.file), float(params.setting),
                           matlab.double([float(i) for i in ids]), [str(params.phase_names[i]) for i in ids],
                           nargout=2)
        return EBSDHandle(self.name, str(hid), int(n))

    def get_ebsd(self, handle: EBSDHandle) -> EBSDData:
        import h5py  # noqa: PLC0415

        fd, tmp = tempfile.mkstemp(suffix=".h5", prefix="ti64_")
        os.close(fd)
        os.remove(tmp)                       # MATLAB creates it
        try:
            info = self.call("ti64.exportEbsd", handle.id, tmp)
            with h5py.File(tmp, "r") as f:
                d = {k: f[k][()] for k in f.keys()}
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        d = {k: (v.ravel() if v.ndim == 2 and min(v.shape) == 1 else v) for k, v in d.items()}
        names = list(info["phaseNames"])
        pmap = np.asarray(info["phaseMap"]).ravel().astype(int)
        phase_names = {int(p): str(n) for p, n in zip(pmap, names) if str(n) != "notIndexed"}
        props = {k[5:]: v for k, v in d.items() if k.startswith("prop_")}
        phase = d["phase"].astype(np.int16)
        phase[phase < 0] = 0
        return EBSDData(d["x"], d["y"], phase, d["quat"], props, phase_names,
                        "hex" if bool(info["isHex"]) else "square", float(info["stepUm"]),
                        int(info["setting"]))

    def cleanup(self, handle: EBSDHandle, p: CleanupParams) -> tuple[EBSDHandle, CleanupReport]:
        d = p.model_dump()
        d["ci_threshold"] = float("nan") if p.ci_threshold is None else float(p.ci_threshold)
        d = {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in d.items()}
        new_id, rep = self.call("ti64.cleanup", handle.id, d, nargout=2)
        rep = {k: int(v) for k, v in dict(rep).items()}
        return EBSDHandle(self.name, str(new_id), rep["n_points"]), CleanupReport(**rep, params=p)

    def _h5(self, fn: str, *args) -> dict:
        """Call a MATLAB exporter that writes an HDF5 file; return its datasets (1-D squeezed)."""
        import h5py  # noqa: PLC0415

        fd, tmp = tempfile.mkstemp(suffix=".h5", prefix="ti64_")
        os.close(fd)
        os.remove(tmp)
        try:
            self.call(fn, *args, tmp, nargout=0)
            with h5py.File(tmp, "r") as f:
                d = {k: f[k][()] for k in f.keys()}
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return {k: (v.ravel() if v.ndim == 2 and min(v.shape) == 1 else v) for k, v in d.items()}

    def calc_grains(self, handle: EBSDHandle, gp: GrainParams) -> GrainsHandle:
        p = {k: float(v) for k, v in gp.model_dump().items()}
        gid, n = self.call("ti64.calcGrains", handle.id, p, nargout=2)
        return GrainsHandle(self.name, str(gid), int(n))

    def get_grains(self, gh: GrainsHandle) -> GrainsData:
        d = self._h5("ti64.exportGrains", gh.id)
        ints = {"id", "phase", "n_px", "grain1", "grain2", "phase1", "phase2", "burgers_type"}
        conv = {"is_edge": bool, "inner": bool}

        def cast(k, v):
            base = k.split("_", 1)[1] if k.startswith(("grain_", "gb_")) else k
            if base in conv:
                return v.astype(bool)
            if base in ints:
                return v.astype(np.int64)
            return v
        grain = {k[6:]: cast(k, v) for k, v in d.items() if k.startswith("grain_")}
        gb = {k[3:]: cast(k, v) for k, v in d.items() if k.startswith("gb_")}
        return GrainsData(d["pixel_grain_id"].astype(np.int64), grain, gb)

    def compute_map(self, gh: GrainsHandle, mp: MapParams) -> np.ndarray:
        p = {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in mp.model_dump().items()}
        return self._h5("ti64.computeMap", gh.id, p)["values"].astype(float)

    def grain_geometry(self, gh: GrainsHandle) -> dict[str, np.ndarray]:
        d = self._h5("ti64.grainGeometry", gh.id)
        out = {k: np.atleast_1d(np.asarray(v, float)).ravel() for k, v in d.items()}
        out["id"] = out["id"].astype(np.int64)
        return out

    def merge_colonies(self, gh: GrainsHandle, cp: ColonyParams) -> GrainsHandle:
        cid, n, _ = self.call("ti64.mergeColonies", gh.id, {"burgers_tol_deg": float(cp.burgers_tol_deg)},
                              nargout=3)
        return GrainsHandle(self.name, str(cid), int(n))

    @staticmethod
    def _num(values) -> object:
        """Python list -> MATLAB numeric row (a plain list would arrive as a cell array)."""
        import matlab  # noqa: PLC0415

        return matlab.double([float(c) for c in values])

    def ipf_colors(self, handle, phase: str, key: str, vec) -> np.ndarray:
        v = self._num(vec)
        return self._h5("ti64.ipfColors", handle.id, phase, key, v)["rgb"].astype(float).reshape(-1, 3)

    def render(self, handle, spec: RenderSpec, out_png):
        import h5py  # noqa: PLC0415

        fd, tmp = tempfile.mkstemp(suffix=".h5", prefix="ti64_r_")
        os.close(fd)
        try:
            with h5py.File(tmp, "w") as f:
                if spec.rgb is not None:
                    f["rgb"] = np.asarray(spec.rgb, float)          # (n,3); MATLAB reads (3,n) -> transposes
                else:
                    f["values"] = np.asarray(spec.values, float)
                if spec.seg is not None and len(spec.seg["x1"]):
                    f["seg"] = np.stack([spec.seg[k] for k in ("x1", "y1", "x2", "y2")], 1).astype(float)
                    f["seg_rgb"] = np.asarray(spec.seg_rgb, float).reshape(-1, 3)
                    if spec.seg_widths is not None:
                        f["seg_w"] = np.asarray(spec.seg_widths, float).ravel()
                texts = self._write_prims(f, spec.prims)
            o = {"cmap": spec.cmap, "clim": self._num(spec.clim if spec.clim else [float("nan")] * 2),
                 "colorbar_label": spec.colorbar_label, "seg_width": float(spec.seg_width), "title": spec.title,
                 "width_cm": float(spec.width_cm), "dpi": float(spec.dpi), "scale_bar": bool(spec.scale_bar),
                 "legend_labels": [l for l, _ in spec.legend],
                 "legend_rgb": self._num([c for _, rgb in spec.legend for c in rgb]),
                 "exact": bool(spec.exact),
                 "xlim": self._num(spec.xlim if spec.xlim else [float("nan")] * 2),
                 "ylim": self._num(spec.ylim if spec.ylim else [float("nan")] * 2),
                 "text_str": [t["text"] for t in texts] or [""],
                 "text_ha": [t.get("ha", "left") for t in texts] or ["left"]}
            out_png = Path(out_png)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            self.call("ti64.render", handle.id, tmp, str(out_png), o, nargout=0)
        finally:
            os.remove(tmp)
        return out_png

    @staticmethod
    def _write_prims(f, prims: list[dict]) -> list[dict]:
        """Annotation primitives into the render HDF5 (layout in +ti64/render.m); returns the texts."""
        lines = [p for p in prims if p["type"] == "line"]
        polys = [p for p in prims if p["type"] == "poly"]
        texts = [p for p in prims if p["type"] == "text"]

        def joined(items):                                   # (N, 2), NaN rows between items
            parts = []
            for it in items:
                parts += [np.asarray(it["xy"], float).reshape(-1, 2), np.full((1, 2), np.nan)]
            return np.concatenate(parts)
        if lines:
            f["pl_xy"] = joined(lines)
            f["pl_style"] = np.array([[*p.get("rgb", (0, 0, 0)), p.get("width", 1.0)] for p in lines], float)
        if polys:
            f["pg_xy"] = joined(polys)
            f["pg_rgb"] = np.array([p.get("rgb", (0, 0, 0)) for p in polys], float).reshape(-1, 3)
        if texts:
            f["tx_xy"] = np.array([p["xy"] for p in texts], float).reshape(-1, 2)
            f["tx_style"] = np.array([[*p.get("rgb", (0, 0, 0)), p.get("size", 8.0)] for p in texts], float)
        return texts

    def measurement_colors(self, spec: RenderSpec) -> tuple[np.ndarray, dict]:
        import h5py  # noqa: PLC0415

        from .base import rgb_to_rgba8  # noqa: PLC0415

        if spec.rgb is not None:
            return rgb_to_rgba8(spec.rgb), {"clim": None, "cmap": None, "colorbar": None}
        fd, tmp = tempfile.mkstemp(suffix=".h5", prefix="ti64_v_")
        os.close(fd)
        try:
            with h5py.File(tmp, "w") as f:
                f["values"] = np.asarray(spec.values, float).ravel()
            d = self._h5("ti64.valueColors", tmp, spec.cmap,
                         self._num(spec.clim if spec.clim else [float("nan")] * 2))
        finally:
            os.remove(tmp)
        lo, hi = (float(c) for c in np.asarray(d["clim"]).ravel())
        return (np.asarray(d["rgba"]).astype(np.uint8).reshape(-1, 4),
                {"clim": (lo, hi), "cmap": spec.cmap, "colorbar": np.asarray(d["lut"]).astype(np.uint8).reshape(-1, 4)})

    def cell_image(self, x, y, colors, grid, background):
        """Regular hexagonal cells drawn by MATLAB (+ti64/hexImage.m -> hexRasterize.m)."""
        import h5py  # noqa: PLC0415

        fd, tin = tempfile.mkstemp(suffix=".h5", prefix="ti64_hx_")
        os.close(fd)
        fd, tout = tempfile.mkstemp(suffix=".h5", prefix="ti64_hy_")
        os.close(fd)
        os.remove(tout)
        try:
            with h5py.File(tin, "w") as f:
                f["x"] = np.asarray(x, float).ravel()
                f["y"] = np.asarray(y, float).ravel()
                f["rgba"] = np.asarray(colors, np.uint8).reshape(-1, 4)
                f["grid"] = np.array([grid.x0, grid.y0, grid.px, grid.py, grid.W, grid.H], float)
                f["bg"] = np.asarray(background, np.uint8).ravel()
            self.call("ti64.hexImage", tin, tout, nargout=0)
            with h5py.File(tout, "r") as f:
                img = f["img"][()]                       # MATLAB H x W x 4 (column-major) -> 4 x W x H here
        finally:
            for t in (tin, tout):
                if os.path.exists(t):
                    os.remove(t)
        return np.ascontiguousarray(np.transpose(img, (2, 1, 0))).astype(np.uint8)

    def render_key(self, handle, phase: str, key: str, vec, out_png, dpi: int = 300):
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        self.call("ti64.renderKey", handle.id, phase, key, self._num(vec), str(out_png), float(dpi),
                  nargout=0)
        return out_png

    def reconstruct(self, handle: EBSDHandle, rp, log_file: str = "") -> tuple[EBSDHandle, dict]:
        d = rp.model_dump()
        sweep = d.pop("sweep")
        d = {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in d.items()}
        d.update(sweepModes=[m for m, _ in sweep], sweepThr=self._num([t for _, t in sweep]), logFile=log_file)
        rid, res = self.call("ti64.reconstruct", handle.id, d, nargout=2)
        out = {}
        for k, v in dict(res).items():
            a = np.asarray(v)
            out[k] = v if isinstance(v, str) else (a.tolist() if a.size != 1 else float(a.ravel()[0]))
        out["sweep"] = np.asarray(res["sweep"], float).reshape(-1, 4).tolist()
        out["mis_share"] = np.asarray(res["mis_share"], float).ravel().tolist()
        out["fit_quantiles"] = np.asarray(res["fit_quantiles"], float).ravel().tolist()
        for k in ("n_noise_px", "n_child_grains", "n_grains_min_ecd", "n_grains_all"):
            out[k] = int(out[k])
        return EBSDHandle(self.name, str(rid), handle.n_points), out

    def get_recon(self, handle: EBSDHandle) -> ReconData:
        d = self._h5("ti64.exportRecon", handle.id)
        for k in ("grain_id", "grain_n_px", "gb_grain1", "gb_grain2"):
            d[k] = d[k].astype(np.int64)
        d["grain_is_edge"] = d["grain_is_edge"].astype(bool)
        d["grain_mean_quat"] = d["grain_mean_quat"].reshape(-1, 4)
        return ReconData.from_arrays(d)

    def ipf_colors_quat(self, handle: EBSDHandle, phase: str, quat: np.ndarray, key: str, vec) -> np.ndarray:
        import h5py  # noqa: PLC0415

        fd, tmp = tempfile.mkstemp(suffix=".h5", prefix="ti64_q_")
        os.close(fd)
        try:
            with h5py.File(tmp, "w") as f:
                f["quat"] = np.asarray(quat, float).reshape(-1, 4)
            rgb = self._h5("ti64.ipfColorsQuat", handle.id, phase, key, self._num(vec), tmp)["rgb"]
        finally:
            os.remove(tmp)
        return np.asarray(rgb, float).reshape(-1, 3)

    def render_chart(self, spec: ChartSpec, out_png):
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        o = {"bar_width": float(spec.bar_width), "xlabel": spec.xlabel, "ylabel": spec.ylabel, "title": spec.title,
             "log_y": bool(spec.log_y), "vline_x": self._num([x for x, _ in spec.vlines]),
             "vline_labels": [lab for _, lab in spec.vlines], "color": self._num(spec.color),
             "width_cm": float(spec.width_cm), "dpi": float(spec.dpi)}
        if spec.curves:
            o["curve_x"] = self._num(np.concatenate([np.r_[np.asarray(x, float), np.nan] for x, _, _ in spec.curves])[:-1])
            o["curve_y"] = self._num(np.concatenate([np.r_[np.asarray(y, float), np.nan] for _, y, _ in spec.curves])[:-1])
            o["curve_labels"] = [lab for _, _, lab in spec.curves]
        self.call("ti64.renderChart", self._num(spec.centers), self._num(spec.heights), o, str(out_png), nargout=0)
        return out_png

    def release(self, handle: EBSDHandle) -> None:
        if self.running:
            try:
                self.call("ti64.store", "remove", handle.id, nargout=0)
            except EngineRestarted:
                pass                             # gone with the old session

    def handles(self) -> list[str]:
        """IDs of the objects in the CURRENT MATLAB session ([] without an engine; after a restart only new ones)."""
        if not self.running:
            return []
        try:
            return [str(h) for h in self.call("ti64.store", "list")]
        except EngineRestarted:
            return []                            # the old objects are gone; callers rebuild them
