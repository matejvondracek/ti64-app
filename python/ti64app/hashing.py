"""Content hashes for recipe outputs and fingerprints for input files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

_EDGE = 4 * 2**20      # bytes hashed from the start and end of large input files


def array_hash(arrays: dict[str, np.ndarray]) -> str:
    """Hash of array CONTENT (names, dtypes, shapes, bytes), independent of the file container."""
    h = hashlib.sha256()
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(name.encode())
        h.update(str(a.dtype).encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return "sha256:" + h.hexdigest()


def json_hash(obj: Any) -> str:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def npz_hash(path: Path) -> str:
    with np.load(path, allow_pickle=False) as z:
        return array_hash({k: z[k] for k in z.files})


def png_hash(path: Path) -> str:
    """Hash of the decoded pixels (RGBA) of an image, independent of PNG metadata/compression."""
    from PIL import Image  # noqa: PLC0415

    # Our own output files: PIL's decompression-bomb limit (179 Mpx) made a 9767 x 22453 px mosaic export fail AFTER
    # it was written. RGBA is converted in row bands (a full RGBA copy is 4 B per pixel); the digest is identical to
    # array_hash({"rgba": full RGBA array}).
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as im:
            im.load()
            W, H = im.size
            h = hashlib.sha256()
            h.update(b"rgba")
            h.update(b"uint8")
            h.update(str((H, W, 4)).encode())
            band = max(1, 2**26 // max(1, 4 * W))
            for y in range(0, H, band):
                h.update(np.ascontiguousarray(np.asarray(im.crop((0, y, W, min(H, y + band))).convert("RGBA")))
                         .tobytes())
    finally:
        Image.MAX_IMAGE_PIXELS = old
    return "sha256:" + h.hexdigest()


def bytes_hash(path: Path) -> str:
    """Hash of a file's bytes (CSV tables, mosaic TIFFs: written deterministically). Streamed (large mosaics)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(16 * 2**20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def xlsx_hash(path: Path) -> str:
    """Hash of the CELL VALUES of every sheet (sheet names in order), independent of the timestamps an
    .xlsx container carries."""
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        content = [[ws.title, [list(r) for r in ws.iter_rows(values_only=True)]] for ws in wb.worksheets]
    finally:
        wb.close()
    return json_hash(content)


def file_fingerprint(path: Path) -> str:
    """Cheap fingerprint of an input file: size + first and last 4 MB (full hash below 8 MB).
    Enough to notice a replaced or edited file without reading 43 GB .up2 files."""
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as fh:
        if size <= 2 * _EDGE:
            h.update(fh.read())
        else:
            h.update(fh.read(_EDGE))
            fh.seek(size - _EDGE)
            h.update(fh.read(_EDGE))
    return f"edge-sha256:{size}:{h.hexdigest()}"
