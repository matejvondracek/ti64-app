"""EDAX .up2 pattern files: header and lazy, chunked pattern access. Read only; never loads the whole file.

Indexing lives in ti64app.modules.indexing. Header (versions >= 3, little endian):
  bytes  0-15  int32 version, int32 pattern width, int32 pattern height, int32 offset of the first pattern
  byte   16    uint8 'extra patterns' flag (1: a hex file stores n_cols patterns in EVERY row, see below)
  bytes 17-24  int32 n columns (= NCOLS_ODD of the .ang), int32 n rows
  byte   25    uint8 hex-grid flag
  bytes 26-41  float64 x step, float64 y step (um)
  bytes 42-57  scan GUID (version >= 4, data offset >= 58), little-endian GUID layout (Data1..Data3 little endian,
               Data4 as bytes) = '# SCANID' of the .ang exported from the same scan, which pairs the two files
Patterns are uint16, row-major, contiguous from the offset.

Hex grids: scan rows alternate n_cols (even rows, 0-based) and n_cols - 1 points (odd rows, shifted by half a step),
as in the .ang. With the extra-patterns flag the file holds n_rows x n_cols patterns: each short row carries ONE
padding pattern, which sits at the END of the row. Found from the data (phase 6.6 fix 4): full-resolution pattern
quality vs the IQ of the paired .ang, both high-pass filtered (smooth shading removed), points with CI >= 0.1:
  JU04 Area 2: even rows r 0.569; odd rows r 0.564 with the padding at the end, 0.367 at the start
  JU04 Area 1: even rows r 0.556; odd rows r 0.552 with the padding at the end, 0.412 at the start
(the same in each of 10 bands of rows). Counting n_cols - 1 patterns per short row (the old reader) shifts every
row pair by one more pattern: r ~ 0.0 everywhere, the diagonal seam of the overview. tests/integration/
test_up2_hex.py repeats the check.

Point order: point i = the i-th data row of the paired .ang (row by row, short rows without padding). read_points() /
iter_point_chunks() give patterns in that order, for PyEBSDIndex (patsin=...) and kikuchipy (lazy dask array via
point_array()); kikuchipy's own .up2 reader and PyEBSDIndex's file reader assume one pattern per point and would
shift hex files with padding.
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

PAD_AT_END = True          # padding pattern of a short hex row: last pattern of the row (see module doc)


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
    extra_patterns: bool = False        # hex: every row stored with n_cols patterns (one padding per short row)
    scan_id: str = ""                   # scan GUID (= '# SCANID' of the .ang), '' if the header has none

    @property
    def padded(self) -> bool:
        return self.hex_grid and self.extra_patterns

    @property
    def n_points(self) -> int:
        """Scan points (= data rows of the paired .ang)."""
        if self.hex_grid:
            return (self.n_rows + 1) // 2 * self.n_cols + self.n_rows // 2 * (self.n_cols - 1)
        return self.n_rows * self.n_cols

    @property
    def n_patterns(self) -> int:
        """Patterns stored in the file (points + padding patterns)."""
        return self.n_rows * self.n_cols if self.padded else self.n_points

    @property
    def n_padding(self) -> int:
        return self.n_patterns - self.n_points

    @property
    def pattern_bytes(self) -> int:
        return self.pattern_width * self.pattern_height * 2

    @property
    def expected_size(self) -> int:
        return self.data_offset + self.n_patterns * self.pattern_bytes

    @property
    def size_ok(self) -> bool:
        return self.expected_size == self.file_size


def _guid(b: bytes) -> str:
    if len(b) < 16 or not any(b[:16]):
        return ""
    return str(uuid.UUID(bytes_le=bytes(b[:16])))


def read_header(path: str | Path) -> Up2Header:
    path = Path(path)
    with open(path, "rb") as fh:
        b = fh.read(58)
    version, w, h, off = struct.unpack_from("<4i", b, 0)
    if version < 3:
        raise ValueError(f"{path.name}: UP2 version {version} has no grid header; not supported")
    extra, ncol, nrow, hexflag, xs, ys = struct.unpack_from("<BiiBdd", b, 16)
    sid = _guid(b[42:58]) if off >= 58 and len(b) >= 58 else ""
    return Up2Header(version, w, h, off, ncol, nrow, bool(hexflag), xs, ys, path.stat().st_size, bool(extra), sid)


def read_patterns(path: str | Path, start: int, count: int, header: Up2Header | None = None) -> np.ndarray:
    """Read `count` patterns starting at FILE index `start` (padding patterns included; memory-mapped)."""
    header = header or read_header(path)
    if start < 0 or start + count > header.n_patterns:
        raise IndexError("pattern range outside the file")
    mm = np.memmap(path, dtype="<u2", mode="r", offset=header.data_offset,
                   shape=(header.n_patterns, header.pattern_height, header.pattern_width))
    return np.array(mm[start:start + count])


def row_length(header: Up2Header, row: int) -> int:
    """Scan points in one row: hex grids alternate n_cols (even rows, 0-based) and n_cols - 1 (odd rows)."""
    return header.n_cols - (1 if header.hex_grid and row % 2 else 0)


def row_start(header: Up2Header, row: int) -> int:
    """File index of the first pattern of a scan row."""
    if header.padded or not header.hex_grid:
        return row * header.n_cols
    return (row // 2) * (2 * header.n_cols - 1) + (header.n_cols if row % 2 else 0)


def pattern_index(header: Up2Header, row: int, col: int) -> int:
    """File index of the pattern at scan row / column (0-based; padding skipped)."""
    if not (0 <= row < header.n_rows) or not (0 <= col < row_length(header, row)):
        raise IndexError(f"row {row}, column {col} is outside the scan ({header.n_rows} rows)")
    lead = 1 if (header.padded and row % 2 and not PAD_AT_END) else 0
    return row_start(header, row) + lead + col


def point_rc(header: Up2Header, point: np.ndarray | int) -> tuple[np.ndarray, np.ndarray]:
    """Scan row / column of point indices (.ang data-row order)."""
    p = np.asarray(point, np.int64)
    if not header.hex_grid:
        return p // header.n_cols, p % header.n_cols
    pair = 2 * header.n_cols - 1
    r2, rem = p // pair, p % pair
    odd = rem >= header.n_cols
    return 2 * r2 + odd, np.where(odd, rem - header.n_cols, rem)


def point_file_index(header: Up2Header, point: np.ndarray | int) -> np.ndarray:
    """File pattern index of point indices (vectorised; padding skipped)."""
    r, c = point_rc(header, point)
    if not header.padded:
        return np.asarray(point, np.int64)
    lead = (r % 2) if not PAD_AT_END else 0
    return r * header.n_cols + lead + c


def pattern_position(header: Up2Header, row: int, col: int) -> tuple[float, float]:
    """(x, y) in um of a scan point (hex grids: odd rows shifted by half a step), as in the .ang."""
    x = (col + (0.5 if header.hex_grid and row % 2 else 0.0)) * header.x_step_um
    return x, row * header.y_step_um


def read_pattern(path: str | Path, row: int, col: int, header: Up2Header | None = None) -> np.ndarray:
    """One pattern (uint16, height x width) at scan row / column; reads only its bytes."""
    header = header or read_header(path)
    return read_patterns(path, pattern_index(header, row, col), 1, header)[0]


def read_row(path: str | Path, row: int, header: Up2Header | None = None, fh=None) -> np.ndarray:
    """All patterns of one scan row (padding dropped), (row_length, h, w) uint16."""
    header = header or read_header(path)
    n = row_length(header, row)
    own = fh is None
    fh = fh or open(path, "rb", buffering=0)
    try:
        fh.seek(header.data_offset + pattern_index(header, row, 0) * header.pattern_bytes)
        a = np.fromfile(fh, dtype="<u2", count=n * header.pattern_height * header.pattern_width)
    finally:
        if own:
            fh.close()
    return a.reshape(n, header.pattern_height, header.pattern_width)


def read_points(path: str | Path, start: int, count: int, header: Up2Header | None = None) -> np.ndarray:
    """Patterns of points start .. start + count - 1 in .ang data-row order (padding skipped)."""
    header = header or read_header(path)
    if start < 0 or start + count > header.n_points:
        raise IndexError("point range outside the scan")
    if not header.padded:
        return read_patterns(path, start, count, header)
    fi = point_file_index(header, np.arange(start, start + count))
    lo, hi = int(fi.min()), int(fi.max()) + 1
    block = read_patterns(path, lo, hi - lo, header)          # contiguous span (at most a few padding patterns more)
    return block[fi - lo]


def iter_point_chunks(path: str | Path, chunk: int = 20_000,
                      header: Up2Header | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """(first point, patterns) in .ang order, `chunk` points at a time: the input for PyEBSDIndex
    (indexer.index_pats(patsin=patterns)) without reading the whole file."""
    header = header or read_header(path)
    for s in range(0, header.n_points, chunk):
        n = min(chunk, header.n_points - s)
        yield s, read_points(path, s, n, header)


def point_array(path: str | Path, chunk: int = 4096):
    """Lazy dask array (n_points, h, w) uint16 in .ang order (padding skipped), e.g. for
    kikuchipy.signals.LazyEBSD(point_array(f)) - a 1-D navigation axis, since kikuchipy has no hex navigation."""
    import dask  # noqa: PLC0415
    import dask.array as da  # noqa: PLC0415

    h = read_header(path)
    parts = []
    for s in range(0, h.n_points, chunk):
        n = min(chunk, h.n_points - s)
        parts.append(da.from_delayed(dask.delayed(read_points)(str(path), s, n, h),
                                     shape=(n, h.pattern_height, h.pattern_width), dtype=np.uint16))
    return da.concatenate(parts, axis=0)


def ang_scan_id(path: str | Path) -> str:
    """'# SCANID' of an .ang (header only), lower case; '' if none."""
    try:
        with open(path, "r", encoding="latin-1") as fh:
            for line in fh:
                if not line.startswith("#"):
                    break
                if "SCANID" in line:
                    return line.split(":", 1)[-1].strip().lower()
    except OSError:
        pass
    return ""


def find_paired_ang(up2_path: str | Path, candidates: list[str | Path] | None = None) -> Path | None:
    """The .ang with the same scan ID as a .up2: among `candidates` (e.g. the project's .ang files) first, then the
    .ang files in the .up2's folder. None when the header has no scan ID or nothing matches."""
    h = read_header(up2_path)
    if not h.scan_id:
        return None
    seen = set()
    pool = [Path(c) for c in (candidates or [])] + sorted(Path(up2_path).parent.glob("*.ang"))
    for a in pool:
        k = str(a).lower()
        if k in seen or not a.is_file():
            continue
        seen.add(k)
        if ang_scan_id(a) == h.scan_id.lower():
            return a
    return None
