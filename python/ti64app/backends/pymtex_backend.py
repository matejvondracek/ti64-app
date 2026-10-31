"""pymtex backend: MTEX for Python (`mtex` 1.0.0 on PyPI), in-process.

Loaded maps stay as pymtex EBSD objects in this process, referenced by handle ID.
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import uuid

import numpy as np

from .. import __version__
from .base import PHASE_NOT_INDEXED, PROP_NAMES, EBSDData, EBSDHandle, LoadParams, file_order


def _arr(v):
    return np.asarray(v() if callable(v) else v)


class PymtexBackend:
    name = "pymtex"

    def __init__(self) -> None:
        self._store: dict[str, tuple[object, int]] = {}  # id -> (EBSD, EDAX setting)

    # -- availability -------------------------------------------------------------------------
    def is_available(self) -> bool:
        return importlib.util.find_spec("mtex") is not None

    def unavailable_reason(self) -> str:
        return "" if self.is_available() else "the 'mtex' package (pymtex) is not installed"

    def versions(self) -> dict[str, str]:
        v = {"app": __version__}
        try:
            v["mtex"] = md.version("mtex")
        except md.PackageNotFoundError:
            v["mtex"] = "missing"
        return v

    # -- objects ------------------------------------------------------------------------------
    def load_ebsd(self, params: LoadParams) -> EBSDHandle:
        from mtex import loadEBSD  # noqa: PLC0415  (heavy import)

        ebsd = loadEBSD(str(params.file), setting=params.setting)
        hid = uuid.uuid4().hex[:12]
        self._store[hid] = (ebsd, params.setting)
        pad = _arr(ebsd.isPadding).ravel()
        return EBSDHandle(self.name, hid, int((~pad).sum()))

    def _get(self, handle: EBSDHandle):
        if handle.backend != self.name or handle.id not in self._store:
            raise KeyError(f"no pymtex object {handle.id}")
        return self._store[handle.id][0]

    def get_ebsd(self, handle: EBSDHandle) -> EBSDData:
        ebsd = self._get(handle)
        keep = ~_arr(ebsd.isPadding).ravel()
        phase_id = _arr(ebsd.phaseId).ravel()[keep].astype(np.int16)   # 0 = notIndexed, else CSList index
        x = _arr(ebsd.x).ravel()[keep]
        y = _arr(ebsd.y).ravel()[keep]
        quat = np.full((x.size, 4), np.nan)
        names: dict[int, str] = {}
        for pid, cs in enumerate(ebsd.CSList):
            name = getattr(cs, "mineral", None)
            if pid == PHASE_NOT_INDEXED or not name:
                continue
            o = ebsd[name].orientations
            m = phase_id == pid
            q = np.stack([_arr(o.a).ravel(), _arr(o.b).ravel(), _arr(o.c).ravel(), _arr(o.d).ravel()], 1)
            if q.shape[0] != m.sum():
                raise RuntimeError(f"pymtex: {name} has {q.shape[0]} orientations for {m.sum()} pixels")
            quat[m] = q
            names[pid] = str(name)
        props = {neutral: _arr(ebsd.prop[eng]).ravel()[keep]
                 for neutral, eng in PROP_NAMES[self.name].items() if eng in ebsd.prop}
        dy = float(np.median(np.diff(np.unique(np.round(y, 4))))) if y.size > 1 else 1.0
        order = file_order(x, y, dy)
        hex_grid = "hex" in str(ebsd).lower()
        return EBSDData(x[order], y[order], phase_id[order], quat[order],
                        {k: v[order] for k, v in props.items()}, names,
                        "hex" if hex_grid else "square",
                        round(float(np.median(np.diff(np.unique(np.round(x[y == y.min()], 4))))), 6),
                        self._store[handle.id][1])

    def release(self, handle: EBSDHandle) -> None:
        self._store.pop(handle.id, None)

    def handles(self) -> list[str]:
        return list(self._store)
