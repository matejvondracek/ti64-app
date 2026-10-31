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
