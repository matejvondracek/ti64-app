"""Sample sheet: what the user enters per sample/map. Never guessed by the app.

Contents: build direction, input state of each map (raw | OIM-cleaned), 4-point-bending geometry
and load, position of the map on the sample.

Units (see docs/decisions.md D2): microstructure in um / deg; specimen geometry in mm, load in N,
stress in MPa. Field names carry the unit. Sample frame: X = beam axis, Y = thickness
(neutral axis at Y = 0), Z = width.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

InputState = Literal["raw", "oim_cleaned"]
Axis = Literal["+x", "-x", "+y", "-y", "+z", "-z"]

_CLEANED = re.compile(r"(clean|cleaned|cleanup|_cl\b|cid|gd\b)", re.IGNORECASE)


def suggest_input_state(filename: str | Path) -> InputState:
    """Auto-suggest the input state from the file name; the user confirms or changes it."""
    return "oim_cleaned" if _CLEANED.search(Path(filename).stem) else "raw"


class BuildDirection(BaseModel):
    """Build direction in MAP coordinates (as loaded with EDAX setting 2, x right, y down)."""
    map_axis: Axis | None = None
    custom_vector: tuple[float, float, float] | None = None

    @model_validator(mode="after")
    def _one(self):
        if self.map_axis is not None and self.custom_vector is not None:
            raise ValueError("give either map_axis or custom_vector, not both")
        return self


class FourPointBending(BaseModel):
    outer_span_mm: float = Field(gt=0)
    inner_span_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)          # along sample Z
    thickness_mm: float = Field(gt=0)      # along sample Y
    load_N: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _spans(self):
        if self.inner_span_mm >= self.outer_span_mm:
            raise ValueError("inner span must be smaller than outer span")
        return self


class MapPosition(BaseModel):
    """Where the EBSD map sits in the sample frame (map origin = first pixel)."""
    origin_x_mm: float = 0.0               # along the beam axis, from the specimen centre
    origin_y_mm: float = 0.0               # through thickness, from the neutral axis
    map_x_axis: Axis = "+x"                # sample-frame direction of map +x
    map_y_axis: Axis = "+y"                # sample-frame direction of map +y


class MapEntry(BaseModel):
    file: str
    input_state: InputState
    position: MapPosition | None = None


class SampleSheet(BaseModel):
    name: str
    material: str = "Ti-6Al-4V"
    process: str = "EBM"
    build_direction: BuildDirection | None = None
    bending: FourPointBending | None = None
    maps: list[MapEntry] = Field(default_factory=list)
    notes: str = ""

    def add_map(self, file: str | Path, input_state: InputState | None = None) -> MapEntry:
        entry = MapEntry(file=str(file), input_state=input_state or suggest_input_state(file))
        self.maps.append(entry)
        return entry
