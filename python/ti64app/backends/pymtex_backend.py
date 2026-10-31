"""pymtex backend: MTEX for Python (`mtex` 1.0.0 on PyPI), in-process. (Skeleton.)"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util

from .. import __version__
from .base import BackendFeatureUnavailable, EBSDData, LoadParams


class PymtexBackend:
    name = "pymtex"

    def is_available(self) -> bool:
        return importlib.util.find_spec("mtex") is not None

    def unavailable_reason(self) -> str:
        return "" if self.is_available() else "the 'mtex' package (pymtex) is not installed"

    def versions(self) -> dict[str, str]:
        v = {"app": __version__}
        try:
            v["mtex"] = md.version("mtex")
        except md.PackageNotFoundError:
            v["mtex"] = "missing"
        return v

    def load_ebsd(self, params: LoadParams) -> EBSDData:
        # next step: loadEBSD(f, setting=2), map grid order -> file rows via (x, y) (see scripts/audit_pymtex.py)
        raise BackendFeatureUnavailable("load_ebsd (not implemented yet)", self.name)
