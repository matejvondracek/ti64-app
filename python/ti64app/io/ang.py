"""TSL/OIM .ang text access that the MTEX loaders do not provide.

- read_header(): grid, step, phases, declared columns.
- data_column_count(): columns actually present in the data rows (the test maps declare 13
  incl. PRIAS, but every row has 10 -> no PRIAS data; docs/data_audit.md).
PRIAS columns, when a future export contains them, will be read here (pymtex 1.0 ignores them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

GRID_KEYS = ("GRID", "XSTEP", "YSTEP", "NCOLS_ODD", "NCOLS_EVEN", "NROWS", "VERSION", "COLUMN_COUNT",
             "COLUMN_HEADERS", "COLUMN_UNITS", "CartesianAlignment", "WorkingDistance",
             "SampleTiltAngle", "x-star", "y-star", "z-star", "SCANID", "OPERATOR")
PHASE_KEYS = ("MaterialName", "Symmetry", "PointGroupID", "SpaceGroupHall", "LatticeConstants")


@dataclass
class AngHeader:
    fields: dict[str, str] = field(default_factory=dict)
    phases: list[dict] = field(default_factory=list)
    n_lines: int = 0

    @property
    def is_hex(self) -> bool:
        return self.fields.get("GRID", "").lower().startswith("hex")

    @property
    def step_um(self) -> float:
        return float(self.fields.get("XSTEP", "nan"))

    @property
    def n_points(self) -> int:
        """Points implied by the header grid."""
        nr = int(self.fields["NROWS"])
        no, ne = int(self.fields["NCOLS_ODD"]), int(self.fields["NCOLS_EVEN"])
        return (nr + 1) // 2 * no + nr // 2 * ne if self.is_hex else nr * no

    @property
    def declared_columns(self) -> list[str]:
        return [c.strip() for c in self.fields.get("COLUMN_HEADERS", "").split(",") if c.strip()]

    @property
    def declares_prias(self) -> bool:
        return any("PRIAS" in c for c in self.declared_columns)


def read_header(path: str | Path) -> AngHeader:
    h = AngHeader()
    phase: dict | None = None
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            h.n_lines += 1
            s = line[1:].strip()
            if not s:
                continue
            first = s.split()[0]
            key, _, val = s.partition(":") if ":" in first else s.partition(" ")
            key, val = key.strip(), val.strip()
            if key == "Phase" and val.isdigit():
                phase = {"id": int(val)}
                h.phases.append(phase)
            elif phase is not None and key in PHASE_KEYS:
                phase[key] = val
            elif key in GRID_KEYS:
                h.fields[key] = val
                if key in ("GRID", "NCOLS_ODD", "NCOLS_EVEN", "NROWS"):
                    phase = None
    return h


class PriasUnavailable(ValueError):
    """The .ang declares PRIAS columns (or not) but the data rows do not contain them."""


def read_prias(path: str | Path, column: int = 0):
    """PRIAS column (0 bottom strip, 1 centre square, 2 top strip) in FILE order.
    pymtex 1.0 does not load PRIAS, so it is read from the .ang text here."""
    import numpy as np  # noqa: PLC0415

    h = read_header(path)
    cols = data_column_count(path)
    if max(cols) < 13 or "PRIAS" not in ",".join(h.declared_columns):
        raise PriasUnavailable(f"{Path(path).name}: no PRIAS data in the rows "
                               f"({max(cols)} columns; header declares {len(h.declared_columns)})")
    return np.loadtxt(path, comments="#", usecols=(10 + column,), dtype=float)


def data_column_count(path: str | Path, n_rows: int = 1000) -> set[int]:
    """Set of column counts in the first n_rows data rows."""
    counts: set[int] = set()
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            counts.add(len(line.split()))
            n_rows -= 1
            if n_rows <= 0:
                break
    return counts
