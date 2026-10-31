"""AnalysisBackend protocol: one interface, two implementations (pymtex, matlab).

Rules (CLAUDE.md): same parameter names, units and defaults in both backends; results in a
backend-neutral schema; each backend also draws its own figures with the shared FigureStyle.
A feature missing in one backend raises BackendFeatureUnavailable with a clear message.

Planned core operations (added to both backends in the same change, with conformance tests):
load_ebsd, cleanup, calc_grains, kam, gb_map, reconstruct_parent (variant graph), grain_size,
ipf_map / phase_map / scalar_map figures. Only the skeleton is defined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel

from ..sample import InputState


class BackendFeatureUnavailable(NotImplementedError):
    """The requested operation is not available in this backend."""

    def __init__(self, op: str, backend: str):
        super().__init__(f"{op} is not available in the {backend} backend")


class BackendUnavailable(RuntimeError):
    """The backend cannot run on this machine (e.g. MATLAB not installed)."""


# ---- parameters ----------------------------------------------------------------------------
class LoadParams(BaseModel):
    file: Path
    input_state: InputState
    setting: Literal[0, 1, 2, 3, 4] = 2       # EDAX setting, docs/decisions.md D1


# ---- backend-neutral results --------------------------------------------------------------
# Property names in the two engines differ (docs/data_audit.md); results always use these names.
PROP_NAMES = {
    "pymtex": {"ci": "ci", "iq": "iq", "fit": "fit", "sem": "sem_signal"},
    "matlab": {"ci": "ci", "iq": "iq", "fit": "fit_prias", "sem": "sem"},
}


@dataclass
class EBSDData:
    """Per-pixel data in FILE row order (one entry per data row of the .ang, no padding)."""
    x: np.ndarray                      # um
    y: np.ndarray                      # um
    phase: np.ndarray                  # file phase numbers: 0 = not indexed, 1 = alpha, 2 = beta
    quat: np.ndarray                   # (n, 4) a, b, c, d; MTEX convention (crystal -> specimen)
    props: dict[str, np.ndarray] = field(default_factory=dict)   # ci, iq, ... (neutral names)
    phase_names: dict[int, str] = field(default_factory=dict)
    grid: Literal["hex", "square"] = "hex"
    step_um: float = 0.0
    backend: str = ""
    versions: dict[str, str] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.x.size)


@runtime_checkable
class AnalysisBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def unavailable_reason(self) -> str: ...

    def versions(self) -> dict[str, str]: ...

    def load_ebsd(self, params: LoadParams) -> EBSDData: ...
