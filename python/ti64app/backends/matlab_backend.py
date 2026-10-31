"""MATLAB backend: MATLAB R2025b + MTEX 7.2 through the MATLAB Engine (`matlabengine==25.2.*`). (Skeleton.)

The engine is started lazily. Large objects stay in the MATLAB session; per-pixel arrays move to
Python through temporary HDF5 files. If MATLAB or the engine is missing the backend reports itself
as unavailable instead of raising.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .. import __version__
from ..settings import REPO_ROOT, load_settings
from .base import BackendFeatureUnavailable, BackendUnavailable, EBSDData, LoadParams

MATLAB_DIR = REPO_ROOT / "matlab"


class MatlabBackend:
    name = "matlab"

    def __init__(self) -> None:
        self._eng = None

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
    def engine(self):
        """Start (once) a MATLAB engine with matlab/ and MTEX on the path."""
        if self._eng is None:
            reason = self.unavailable_reason()
            if reason:
                raise BackendUnavailable(reason)
            import matlab.engine  # noqa: PLC0415  (optional dependency)

            eng = matlab.engine.start_matlab("-nodesktop")
            eng.addpath(str(MATLAB_DIR), nargout=0)
            eng.ti64.setupMtex(load_settings().paths.mtex_path, nargout=0)
            self._eng = eng
        return self._eng

    def close(self) -> None:
        if self._eng is not None:
            self._eng.quit()
            self._eng = None

    def versions(self) -> dict[str, str]:
        eng = self.engine()
        return {"app": __version__, "matlab": str(eng.version()), "mtex": str(eng.getMTEXpref("version"))}

    def load_ebsd(self, params: LoadParams) -> EBSDData:
        # next step: matlab/+ti64/loadEbsd.m (EBSD.load(f,'setting',2)), oldId -> file rows, HDF5 transfer
        raise BackendFeatureUnavailable("load_ebsd (not implemented yet)", self.name)
