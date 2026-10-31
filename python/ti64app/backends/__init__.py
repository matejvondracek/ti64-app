"""Backend registry. Use get_backend(name); never import a backend from Python-only modules."""
from __future__ import annotations

from .base import AnalysisBackend, BackendFeatureUnavailable, BackendUnavailable  # noqa: F401
from .matlab_backend import MatlabBackend
from .pymtex_backend import PymtexBackend

_BACKENDS = {"pymtex": PymtexBackend, "matlab": MatlabBackend}
_instances: dict[str, AnalysisBackend] = {}


def backend_names() -> list[str]:
    return list(_BACKENDS)


def get_backend(name: str) -> AnalysisBackend:
    if name not in _BACKENDS:
        raise KeyError(f"unknown backend {name!r}; choose one of {backend_names()}")
    if name not in _instances:
        _instances[name] = _BACKENDS[name]()
    return _instances[name]


def backend_status() -> dict[str, str]:
    """'' for available backends, otherwise the reason (shown as 'unavailable' in the GUI)."""
    return {n: get_backend(n).unavailable_reason() for n in _BACKENDS}
