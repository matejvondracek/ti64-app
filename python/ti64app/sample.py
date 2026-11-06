"""Sample sheet, one per EBSD map: entered by the user (GUI or MCP), never guessed by the app.

Fields: specimen ID, map file, input state (raw | OIM-cleaned), build direction (required),
4-point-bending geometry and load, position of the map on the specimen.

Conventions (docs/decisions.md):
- Map frame = the .ang loaded with EDAX setting 2: x to the right, y DOWN, z into the screen (D1).
- Build direction as an in-plane angle: measured from map +x toward map +y (D4). Because y points
  down, positive angles turn clockwise on screen. Example JU02 (thesis EBMX): S = -y -> 270 deg.
- Units (D2): microstructure um/deg, specimen geometry mm, load N, stress MPa; names carry the unit.
- Sample frame (CLAUDE.md): X = beam axis, Y = thickness (neutral axis at Y = 0), Z = width.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

InputState = Literal["raw", "oim_cleaned"]
Axis = Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

_CLEANED = re.compile(r"clean", re.IGNORECASE)


def suggest_input_state(filename: str | Path) -> InputState:
    """Suggestion for the GUI/MCP default; the user confirms or changes it."""
    return "oim_cleaned" if _CLEANED.search(Path(filename).stem) else "raw"


def map_id_from_file(filename: str | Path) -> str:
    """Short map ID from a file name, e.g. 'EBM_Ti64_JU02_C_Area 3_...ang' -> 'JU02_C_Area3'."""
    stem = Path(filename).stem
    m = re.search(r"([A-Z]{2}\d{2})_?([A-Z])?_?(Area\s*\d+)?", stem)
    if m:
        parts = [p.replace(" ", "") for p in m.groups() if p]
        return "_".join(parts)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem)[:40]


class BuildDirection(BaseModel):
    """Build direction in MAP coordinates. Give exactly one of `vector` or `angle_deg`."""
    vector: tuple[float, float, float] | None = None
    angle_deg: float | None = None          # in-plane, from +x toward +y (clockwise on screen)

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.vector is None) == (self.angle_deg is None):
            raise ValueError("build direction: give exactly one of 'vector' or 'angle_deg'")
        if self.vector is not None and math.hypot(*self.vector) < 1e-9:
            raise ValueError("build direction vector must not be zero")
        return self

    @property
    def unit_vector(self) -> tuple[float, float, float]:
        if self.angle_deg is not None:
            a = math.radians(self.angle_deg)
            v = (math.cos(a), math.sin(a), 0.0)
        else:
            n = math.sqrt(sum(c * c for c in self.vector))
            v = tuple(c / n for c in self.vector)
        return tuple(0.0 if abs(c) < 1e-12 else c for c in v)


class FourPointBending(BaseModel):
    outer_span_mm: float = Field(gt=0)
    inner_span_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)            # along sample Z
    thickness_mm: float = Field(gt=0)        # along sample Y
    load_N: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _spans(self):
        if self.inner_span_mm >= self.outer_span_mm:
            raise ValueError("inner span must be smaller than outer span")
        return self


class MapPosition(BaseModel):
    """Where the map sits on the specimen: sample-frame position of the map origin (first pixel) and
    which sample axes the map +x / +y point along."""
    origin_X_mm: float = 0.0                 # along the beam axis, from the specimen centre
    origin_Y_mm: float = 0.0                 # through the thickness, from the neutral axis
    origin_Z_mm: float = 0.0                 # across the width, from the centre line
    map_x_axis: Axis = "+X"
    map_y_axis: Axis = "-Y"

    @model_validator(mode="after")
    def _orthogonal(self):
        if self.map_x_axis[1] == self.map_y_axis[1]:
            raise ValueError("map x and y axes must be along different sample axes")
        return self


class SampleSheet(BaseModel):
    map_id: str
    specimen_id: str = Field(min_length=1)
    map_file: str
    input_state: InputState
    build_direction: BuildDirection
    bending: FourPointBending | None = None
    position: MapPosition | None = None
    material: str = "Ti-6Al-4V"
    process: str = "EBM"
    notes: str = ""

    @field_validator("map_id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v):
            raise ValueError("map_id: letters, digits, '_', '-', '.' only (max 64)")
        return v


# ---- phase 6.6: Project -> Sample -> files -----------------------------------------------------------
# SampleInfo = one specimen (entered once); MapInfo = one linked file, linked to its sample. A file's
# effective sheet (SampleSheet above, used by every EBSD op) = its MapInfo + the inherited sample values.
Role = Literal["tension", "middle", "compression"]
FileKind = Literal["ang", "image", "up2", "other"]
INHERITED = ("material", "process", "heat_treatment", "bending")       # sample values a file may override
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SAMPLE_IN_NAME = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{2})(?!\d)")
IMAGE_SUFFIXES = {".bmp", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _check_id(v: str, what: str) -> str:
    if not _ID.fullmatch(v):
        raise ValueError(f"{what}: letters, digits, '_', '-', '.' only (max 64)")
    return v


class SampleInfo(BaseModel):
    """Specimen data, entered once and inherited by the sample's files."""
    id: str                                   # specimen ID, e.g. JU04
    material: str = "Ti-6Al-4V"
    process: str = "EBM"
    heat_treatment: str = ""
    bending: FourPointBending | None = None
    build_direction_specimen: Axis | None = None      # in the specimen frame (X beam, Y thickness, Z width)
    tension_side: Literal["+Y", "-Y"] | None = None   # which face is in tension (drives the role suggestion)
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _safe(cls, v: str) -> str:
        return _check_id(v, "sample ID")


class MapInfo(BaseModel):
    """One linked file (by absolute path, never copied) and its map sheet. `None` = not entered / from sample."""
    id: str                                   # map ID (= map_id of the recipe steps)
    file: str                                 # absolute path
    kind: FileKind = "other"
    sample_id: str | None = None              # None = Unassigned
    size: int | None = None                   # when linked / last used by a step
    mtime: float | None = None
    input_state: InputState | None = None
    build_direction: BuildDirection | None = None
    position: MapPosition | None = None
    role: Role | None = None
    notes: str = ""
    material: str | None = None               # per-file overrides of the sample values (INHERITED)
    process: str | None = None
    heat_treatment: str | None = None
    bending: FourPointBending | None = None

    @field_validator("id")
    @classmethod
    def _safe(cls, v: str) -> str:
        return _check_id(v, "map ID")


def file_kind(path: str | Path) -> FileKind | None:
    """Kind of a file to link; None for JEOL .txt sidecars (shown with their image, never listed)."""
    s = Path(path).suffix.lower()
    if s == ".ang":
        return "ang"
    if s in IMAGE_SUFFIXES:
        return "image"
    if s == ".up2":
        return "up2"
    if s == ".txt" and any(Path(path).with_suffix(x).is_file() for x in (".bmp", ".tif", ".tiff", ".BMP", ".TIF")):
        return None
    return "other"


def suggest_sample_id(path: str | Path) -> str | None:
    """Sample ID from the file name, else from the folder names: JU04 in 9445_MVH1_JU04_SED_1.bmp,
    JU02 in EBM_Ti64_JU02_C_Area 3.ang. A suggestion only (the user confirms the list)."""
    p = Path(path)
    for part in [p.stem, *[q.name for q in p.parents][:3]]:
        m = _SAMPLE_IN_NAME.search(part)
        if m:
            return m.group(1)
    return None


def effective(m: MapInfo, s: SampleInfo | None, which: str) -> tuple[object, bool]:
    """(value, inherited) of an inheritable field."""
    own = getattr(m, which)
    if own is not None:
        return own, False
    return (getattr(s, which) if s is not None else None), True


def effective_sheet(m: MapInfo, s: SampleInfo | None) -> SampleSheet:
    """The SampleSheet the EBSD ops use: map values + inherited sample values. Raises with a plain message if
    something the user must enter is missing (never guessed)."""
    miss = []
    if s is None:
        miss.append("assign the file to a sample")
    if m.input_state is None:
        miss.append("input state (raw / OIM-cleaned)")
    if m.build_direction is None:
        miss.append("build direction in map coordinates")
    if miss:
        raise KeyError(f"map {m.id!r} is not complete; enter on the Files tab: " + ", ".join(miss))
    return SampleSheet(map_id=m.id, specimen_id=s.id, map_file=m.file, input_state=m.input_state,
                       build_direction=m.build_direction, bending=effective(m, s, "bending")[0], position=m.position,
                       material=effective(m, s, "material")[0] or "", process=effective(m, s, "process")[0] or "",
                       notes=m.notes)


def split_sheet(sheet: SampleSheet, sample: SampleInfo | None) -> tuple[SampleInfo, dict]:
    """Legacy SampleSheet -> (SampleInfo, MapInfo fields). Values that differ from an existing sample become
    per-file overrides, so effective_sheet() gives the same sheet back."""
    if sample is None:
        sample = SampleInfo(id=sheet.specimen_id, material=sheet.material, process=sheet.process,
                            bending=sheet.bending)
    m = {"input_state": sheet.input_state, "build_direction": sheet.build_direction, "position": sheet.position,
         "notes": sheet.notes}
    for k in ("material", "process", "bending"):
        if getattr(sheet, k) != getattr(sample, k):
            m[k] = getattr(sheet, k)
    return sample, m


def suggest_role(m: MapInfo, s: SampleInfo | None) -> tuple[Role | None, str]:
    """Role suggested from the position through the thickness (sample Y, neutral axis at 0) and the sample's
    tension side. Returns (role, reason); never guesses the tension side."""
    if m.position is None:
        return None, "enter the position on the specimen first"
    y = m.position.origin_Y_mm
    b = effective(m, s, "bending")[0]
    band = 0.1 * b.thickness_mm / 2 if b else 0.05
    if abs(y) <= band:
        return "middle", f"|Y| = {abs(y):g} mm ≤ {band:g} mm from the neutral axis"
    side = s.tension_side if s else None
    if side is None:
        return None, "set the tension side (+Y / -Y) on the sample sheet"
    role = "tension" if (y > 0) == (side == "+Y") else "compression"
    return role, f"Y = {y:g} mm, tension side {side}"
