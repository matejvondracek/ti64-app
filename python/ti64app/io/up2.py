"""EDAX .up2 pattern files: header and lazy, chunked pattern access. Read only; never loads the whole file.

Indexing lives in ti64app.modules.indexing. Layout (versions >= 3, little endian):
  int32 version, int32 pattern width, int32 pattern height, int32 offset of first pattern,
  uint8 non-contiguous, int32 n columns, int32 n rows, uint8 hex flag, float64 x step, float64 y step;
  patterns are uint16, row-major, contiguous from the offset.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Up2Header:
    version: int
    pattern_width: int
    pattern_height: int
    data_offset: int
    n_cols: int
    n_rows: int
    hex_grid: bool
    x_step_um: float
    y_step_um: float
    file_size: int

    @property
    def n_patterns(self) -> int:
        if self.hex_grid:
            return (self.n_rows + 1) // 2 * self.n_cols + self.n_rows // 2 * (self.n_cols - 1)
        return self.n_rows * self.n_cols

    @property
    def pattern_bytes(self) -> int:
        return self.pattern_width * self.pattern_height * 2

    @property
    def expected_size(self) -> int:
        return self.data_offset + self.n_patterns * self.pattern_bytes

    @property
    def size_ok(self) -> bool:
        return self.expected_size == self.file_size


def read_header(path: str | Path) -> Up2Header:
    path = Path(path)
    with open(path, "rb") as fh:
        b = fh.read(42)
    version, w, h, off = struct.unpack_from("<4i", b, 0)
    if version < 3:
        raise ValueError(f"{path.name}: UP2 version {version} has no grid header; not supported")
    _noncontig, ncol, nrow, hexflag, xs, ys = struct.unpack_from("<BiiBdd", b, 16)
    return Up2Header(version, w, h, off, ncol, nrow, bool(hexflag), xs, ys, path.stat().st_size)


def read_patterns(path: str | Path, start: int, count: int, header: Up2Header | None = None) -> np.ndarray:
    """Read `count` patterns starting at index `start` (memory-mapped, only those bytes)."""
    header = header or read_header(path)
    if start < 0 or start + count > header.n_patterns:
        raise IndexError("pattern range outside the file")
    mm = np.memmap(path, dtype="<u2", mode="r", offset=header.data_offset,
                   shape=(header.n_patterns, header.pattern_height, header.pattern_width))
    return np.array(mm[start:start + count])
