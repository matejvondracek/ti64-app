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


class CleanupParams(BaseModel):
    """Cleanup of a loaded map; the input map is never changed (a new map is returned).
    Order: CI threshold -> wild-spike removal -> noise removal (EBSD_part2.m) -> fill -> filter.
    Grains for noise/spike detection use alpha = 0 (MTEX 7 / pymtex), so not-indexed regions stay
    separate grains as in MTEX 6.1 (docs/decisions.md D7)."""
    ci_threshold: float | None = None          # pixels with CI < this become not indexed
    spike_removal: bool = False                # 1-pixel indexed grains at spike_angle_deg -> not indexed
    spike_angle_deg: float = 5.0
    noise_angle_deg: float = 3.0               # EBSD_part2 par.cleanAngle
    noise_max_indexed_px: int = 2              # EBSD_part2 par.noiseMaxIndexed (indexed grains <= this removed)
    noise_max_void_px: int = 4                 # EBSD_part2 par.noiseMaxVoid (not-indexed regions <= this filled)
    fill: bool = True                          # grain-dilation-like fill of removed noise + small voids
    hq_filter: bool = False                    # optional halfQuadraticFilter on the orientations
    hq_alpha: float = 0.5                      # regularisation strength of the filter
    hq_threshold_deg: float = 15.0             # filter does not smooth across misorientations above this


CLEANUP_PRESETS: dict[str, CleanupParams] = {
    # Setup guide 'Method notes': raw = CI < 0.1 removed first, then the EBSD_part2 noise cleanup
    "raw": CleanupParams(ci_threshold=0.1),
    # OIM-cleaned: CI already modified by OIM -> no CI filter; same noise cleanup (JU02 reference)
    "oim_cleaned": CleanupParams(ci_threshold=None),
}


def cleanup_params_for(input_state: str, overrides: dict | None = None) -> CleanupParams:
    """Preset selected by the raw / OIM-cleaned toggle, with explicit user overrides on top."""
    base = CLEANUP_PRESETS[input_state].model_dump()
    base.update({k: v for k, v in (overrides or {}).items()})
    return CleanupParams.model_validate(base)


class CleanupReport(BaseModel):
    n_points: int
    n_indexed_before: int
    n_ci_removed: int
    n_spikes_removed: int
    n_noise_indexed_removed: int               # pixels of removed indexed noise grains
    n_small_void_px: int                       # not-indexed pixels in voids <= noise_max_void_px
    n_filled: int
    n_unfilled: int                            # pixels meant to be filled that had no indexed neighbour
    n_indexed_after: int
    n_filtered_changed: int = 0                # pixels changed by > 0.1 deg by the filter
    params: CleanupParams


class GrainParams(BaseModel):
    """Alpha grain segmentation (user decision 2026-10-31): grains bounded by HAGB >= hagb_deg;
    boundaries lagb_deg..hagb_deg inside grains are LAGB (MTEX calcGrains 'angle', [hagb lagb])."""
    hagb_deg: float = 15.0
    lagb_deg: float = 2.0
    alpha: float = 3.1                        # MTEX/pymtex default for gridded maps, passed explicitly
    min_px: int = 1


# Burgers alpha/alpha misorientation types between variants of one beta grain (angles, deg);
# computed in each backend from orientation.Burgers(csBeta, csAlpha) variants, these are labels only.
BURGERS_TYPES = (10.53, 60.0, 60.83, 63.26, 90.0)


class MapParams(BaseModel):
    """Per-pixel orientation maps computed inside grains (decided per phase)."""
    kind: Literal["kam", "grod", "gos", "gnd"]
    kam_order: int = 1                        # kernel order (1st..3rd neighbours)
    kam_threshold_deg: float = 5.0            # cutoff: pairs above it are ignored (grain boundaries)
    gnd_phase: str = "Ti (alpha)"
    poisson: float = 0.34                     # Ti; screw line energy = 1 - nu (MTEX GND example)
    gnd_stencil: Literal["1hop", "oneSided", "full"] = "1hop"   # orientation gradient stencil (decisions D8)


@dataclass(frozen=True)
class GrainsHandle:
    backend: str
    id: str
    n_grains: int


@dataclass
class GrainsData:
    """Backend-neutral grains + boundaries. Pixel arrays in FILE order; grain ids are the backend's
    (equal numbering is not required; conformance compares partitions and statistics)."""
    pixel_grain_id: np.ndarray                # 0 = none
    grain: dict[str, np.ndarray]              # id, phase, n_px, area_um2, ecd_um, is_edge, mean_quat (n,4)
    boundary: dict[str, np.ndarray]           # x1 y1 x2 y2 length_um phase1 phase2 grain1 grain2
                                              # mis_deg (NaN unless same indexed phase) burgers_type (1..5, 0)
                                              # burgers_dist_deg inner (bool: LAGB inside a grain)

    def to_arrays(self) -> dict[str, np.ndarray]:
        out = {"pixel_grain_id": self.pixel_grain_id}
        out.update({f"grain_{k}": v for k, v in self.grain.items()})
        out.update({f"gb_{k}": v for k, v in self.boundary.items()})
        return out


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


@dataclass
class RenderSpec:
    """What a backend renderer draws (each backend draws its own figures, CLAUDE.md).
    Per-pixel arrays are in FILE order. Either `rgb` (n,3 in 0..1, NaN = no value, drawn white)
    or `values` (n, scalar, coloured with `cmap`/`clim` by the backend) is given.
    Overlays (boundaries, crack paths, ...) arrive as vector data: segments + colours."""
    rgb: np.ndarray | None = None
    values: np.ndarray | None = None
    cmap: str = "parula"
    clim: tuple[float, float] | None = None
    colorbar_label: str = ""
    seg: dict[str, np.ndarray] | None = None          # x1 y1 x2 y2 (um)
    seg_rgb: np.ndarray | None = None                 # (m, 3)
    seg_width: float = 0.75
    legend: list[tuple[str, tuple[float, float, float]]] = field(default_factory=list)
    title: str = ""
    width_cm: float = 12.0
    dpi: int = 300
    scale_bar: bool = True


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
