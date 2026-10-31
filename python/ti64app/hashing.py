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
