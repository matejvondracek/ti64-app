"""MATLAB backend: MATLAB R2025b + MTEX 7.2 through the MATLAB Engine (`matlabengine==25.2.*`).

- One persistent engine per Python process, started lazily on first use (startup ~10-20 s).
- MTEX objects stay in the MATLAB session (ti64.store) and are referenced by ID.
- Per-pixel arrays move to Python through temporary HDF5 files (ti64.exportEbsd + h5py),
  never element by element through the engine.
- If MATLAB, the engine package or MTEX is missing, the backend reports itself as unavailable.
"""
from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import threading
from pathlib import Path

import numpy as np

from .. import __version__
from ..settings import REPO_ROOT, load_settings
from .base import (BackendUnavailable, ChartSpec, CleanupParams, CleanupReport, EBSDData, EBSDHandle,
                   GrainParams, GrainsData, GrainsHandle, LoadParams, MapParams, ReconData, RenderSpec)

MATLAB_DIR = REPO_ROOT / "matlab"


class MatlabBackend:
    name = "matlab"

    def __init__(self) -> None:
        self._eng = None
        self._lock = threading.Lock()
        self._log = io.StringIO()

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
                import matlab.engine  # noqa: PLC0415  (optional dependency)

                eng = matlab.engine.start_matlab("-nodesktop -nosplash")
                eng.addpath(str(MATLAB_DIR), nargout=0)
                eng.ti64.setupMtex(load_settings().paths.mtex_path, nargout=0, stdout=self._log)
                self._eng = eng
            return self._eng

    def call(self, fn: str, *args, nargout: int = 1):
        """Call a MATLAB function by name; MTEX console output goes to self.log."""
        return self.engine().feval(fn, *args, nargout=nargout, stdout=self._log, stderr=self._log)

    @property
    def log(self) -> str:
        return self._log.getvalue()

    def pid(self) -> int:
        return int(self.call("feature", "getpid"))

    def close(self) -> None:
        with self._lock:
            if self._eng is not None:
                try:
                    self._eng.quit()
                finally:
                    self._eng = None

    def versions(self) -> dict[str, str]:
        return {"app": __version__, "matlab": str(self.call("version")),
                "mtex": str(self.call("getMTEXpref", "version"))}

    # -- objects ------------------------------------------------------------------------------
    def load_ebsd(self, params: LoadParams) -> EBSDHandle:
        hid, n = self.call("ti64.loadEbsd", str(params.file), float(params.setting), nargout=2)
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
            o = {"cmap": spec.cmap, "clim": self._num(spec.clim if spec.clim else [float("nan")] * 2),
                 "colorbar_label": spec.colorbar_label, "seg_width": float(spec.seg_width), "title": spec.title,
                 "width_cm": float(spec.width_cm), "dpi": float(spec.dpi), "scale_bar": bool(spec.scale_bar),
                 "legend_labels": [l for l, _ in spec.legend],
                 "legend_rgb": self._num([c for _, rgb in spec.legend for c in rgb])}
            out_png = Path(out_png)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            self.call("ti64.render", handle.id, tmp, str(out_png), o, nargout=0)
        finally:
            os.remove(tmp)
        return out_png

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
        self.call("ti64.renderChart", self._num(spec.centers), self._num(spec.heights), o, str(out_png), nargout=0)
        return out_png

    def release(self, handle: EBSDHandle) -> None:
        if self.running:
            self.call("ti64.store", "remove", handle.id, nargout=0)

    def handles(self) -> list[str]:
        if not self.running:
            return []
        return [str(h) for h in self.call("ti64.store", "list")]
