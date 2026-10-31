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
from .base import BackendUnavailable, EBSDData, EBSDHandle, LoadParams

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

    def release(self, handle: EBSDHandle) -> None:
        if self.running:
            self.call("ti64.store", "remove", handle.id, nargout=0)

    def handles(self) -> list[str]:
        if not self.running:
            return []
        return [str(h) for h in self.call("ti64.store", "list")]
