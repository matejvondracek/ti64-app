"""AnalysisBackend protocol: one interface, two implementations (pymtex, matlab).

Rules (CLAUDE.md): same parameter names, units and defaults in both backends; results in a
backend-neutral schema; each backend also draws its own figures with the shared FigureStyle.
A feature missing in one backend raises BackendFeatureUnavailable with a clear message.

Objects stay inside the backend (pymtex: Python objects, matlab: the MATLAB session) and are
referenced by an EBSDHandle; arrays are fetched on demand with get_ebsd().

Planned core operations (added to both backends in the same change, with conformance tests):
cleanup, calc_grains, kam, gb_map, reconstruct_parent (variant graph), grain_size, figures.
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
    input_state: InputState                # part of the cache key; selects later presets
    setting: Literal[0, 1, 2, 3, 4] = 2    # EDAX setting, docs/decisions.md D1


# ---- handles and backend-neutral results --------------------------------------------------
@dataclass(frozen=True)
class EBSDHandle:
    backend: str
    id: str
    n_points: int


# Property names in the two engines differ (docs/data_audit.md); results always use the keys.
PROP_NAMES = {
    "pymtex": {"ci": "ci", "iq": "iq", "fit": "fit", "sem": "sem_signal"},
    "matlab": {"ci": "ci", "iq": "iq", "fit": "fit_prias", "sem": "sem"},
}
PHASE_NOT_INDEXED = 0


@dataclass
class EBSDData:
    """Per-pixel data in FILE row order (one entry per data row of the .ang, no padding)."""
    x: np.ndarray                      # um
    y: np.ndarray                      # um
    phase: np.ndarray                  # file phase numbers: 0 = not indexed, 1, 2, ... as in the header
    quat: np.ndarray                   # (n, 4) a, b, c, d; MTEX convention (crystal -> specimen); NaN if not indexed
    props: dict[str, np.ndarray] = field(default_factory=dict)   # ci, iq, fit, sem (neutral names)
    phase_names: dict[int, str] = field(default_factory=dict)
    grid: Literal["hex", "square"] = "hex"
    step_um: float = 0.0
    setting: int = 2

    @property
    def n(self) -> int:
        return int(self.x.size)

    def to_arrays(self) -> dict[str, np.ndarray]:
        out = {"x": self.x, "y": self.y, "phase": self.phase, "quat": self.quat}
        out.update({f"prop_{k}": v for k, v in self.props.items()})
        return out

    def meta(self) -> dict:
        return {"phase_names": {str(k): v for k, v in self.phase_names.items()}, "grid": self.grid,
                "step_um": self.step_um, "setting": self.setting, "n": self.n}

    def save_npz(self, path: Path) -> None:
        np.savez(path, **self.to_arrays())

    @classmethod
    def from_npz(cls, path: Path, meta: dict) -> "EBSDData":
        with np.load(path, allow_pickle=False) as z:
            props = {k[5:]: z[k] for k in z.files if k.startswith("prop_")}
            return cls(z["x"], z["y"], z["phase"], z["quat"], props,
                       {int(k): v for k, v in meta["phase_names"].items()},
                       meta["grid"], meta["step_um"], meta["setting"])


def file_order(x: np.ndarray, y: np.ndarray, dy: float) -> np.ndarray:
    """Permutation that puts pixels into OIM file order (row-major: rows by y, then increasing x)."""
    row = np.round(y / dy).astype(np.int64)
    return np.lexsort((x, row))


@runtime_checkable
class AnalysisBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def unavailable_reason(self) -> str: ...

    def versions(self) -> dict[str, str]: ...

    def load_ebsd(self, params: LoadParams) -> EBSDHandle: ...

    def get_ebsd(self, handle: EBSDHandle) -> EBSDData: ...

    def release(self, handle: EBSDHandle) -> None: ...

    def handles(self) -> list[str]: ...
