"""pymtex backend: MTEX for Python (`mtex` 1.0.0 on PyPI), in-process.

Loaded maps stay as pymtex EBSD objects in this process, referenced by handle ID.
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import uuid

import numpy as np

from .. import __version__
from .base import (PHASE_NOT_INDEXED, PROP_NAMES, CleanupParams, CleanupReport, EBSDData, EBSDHandle,
                   LoadParams, file_order)
from .hexgrid import dilation_fill, neighbour_table
from .pymtex_analysis import PymtexAnalysis

# per-pixel cleanup flags (prop 'ti64_cleanup', same codes in the MATLAB backend)
FLAG_KEPT, FLAG_CI, FLAG_SPIKE, FLAG_NOISE, FLAG_FILLED, FLAG_UNFILLED = 0, 1, 2, 3, 4, 5


def _arr(v):
    return np.asarray(v() if callable(v) else v)


class PymtexBackend(PymtexAnalysis):
    name = "pymtex"

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}      # id -> {"ebsd", "setting", ...}

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
    def _put(self, ebsd, setting: int) -> EBSDHandle:
        hid = uuid.uuid4().hex[:12]
        self._store[hid] = {"ebsd": ebsd, "setting": setting}
        pad = _arr(ebsd.isPadding).ravel()
        return EBSDHandle(self.name, hid, int((~pad).sum()))

    def load_ebsd(self, params: LoadParams) -> EBSDHandle:
        from mtex import loadEBSD  # noqa: PLC0415  (heavy import)

        return self._put(loadEBSD(str(params.file), setting=params.setting), params.setting)

    def _get(self, handle: EBSDHandle):
        if handle.backend != self.name or handle.id not in self._store or "ebsd" not in self._store[handle.id]                 or "grains" in self._store[handle.id]:
            raise KeyError(f"no pymtex object {handle.id}")
        return self._store[handle.id]["ebsd"]

    def get_ebsd(self, handle: EBSDHandle) -> EBSDData:
        ebsd = self._get(handle)
        keep = ~_arr(ebsd.isPadding).ravel()
        phase_id = _arr(ebsd.phaseId).ravel()[keep].astype(np.int16)   # 0 = notIndexed, else CSList index
        x = _arr(ebsd.x).ravel()[keep]
        y = _arr(ebsd.y).ravel()[keep]
        quat = np.array(ebsd.rotations.data, dtype=float).reshape(-1, 4)[keep]
        quat[phase_id == PHASE_NOT_INDEXED] = np.nan
        names = {pid: str(cs.mineral) for pid, cs in enumerate(ebsd.CSList)
                 if pid != PHASE_NOT_INDEXED and getattr(cs, "mineral", None)}
        props = {neutral: _arr(ebsd.prop[eng]).ravel()[keep]
                 for neutral, eng in PROP_NAMES[self.name].items() if eng in ebsd.prop}
        props.update({k[5:]: _arr(v).ravel()[keep] for k, v in ebsd.prop.items() if k.startswith("ti64_")})
        dy = float(np.median(np.diff(np.unique(np.round(y, 4))))) if y.size > 1 else 1.0
        order = file_order(x, y, dy)
        hex_grid = "hex" in str(ebsd).lower()
        return EBSDData(x[order], y[order], phase_id[order], quat[order],
                        {k: v[order] for k, v in props.items()}, names,
                        "hex" if hex_grid else "square",
                        round(float(np.median(np.diff(np.unique(np.round(x[y == y.min()], 4))))), 6),
                        self._store[handle.id]["setting"])

    def release(self, handle: EBSDHandle) -> None:
        self._store.pop(handle.id, None)

    def handles(self) -> list[str]:
        return list(self._store)

    # -- cleanup ------------------------------------------------------------------------------
    @staticmethod
    def _grains_alpha0(ebsd, angle_deg: float):
        """Grains with alpha = 0 (not-indexed regions stay separate grains, as in MTEX 6.1).
        Returns per-pixel grain id (flat), and per-grain-id lookup arrays numPixel / isIndexed."""
        from mtex import calcGrains, degree  # noqa: PLC0415

        g = calcGrains(ebsd, angle=angle_deg * degree, alpha=0)
        gid = _arr(ebsd.prop["grainId"]).ravel().astype(np.int64)
        ids = _arr(g.id).ravel().astype(np.int64)
        size = max(int(ids.max(initial=0)), int(gid.max(initial=0))) + 1
        npx = np.zeros(size, np.int64)
        idx = np.zeros(size, bool)
        npx[ids] = _arr(g.numPixel).ravel()
        idx[ids] = _arr(g.isIndexed).ravel()
        del ebsd.prop["grainId"]
        return gid, npx, idx

    def cleanup(self, handle: EBSDHandle, p: CleanupParams) -> tuple[EBSDHandle, CleanupReport]:
        src_map = self._get(handle)
        w = src_map.copy()                                   # the input map stays untouched
        pad = _arr(w.isPadding).ravel()
        q = w.rotations.data.reshape(-1, 4)
        ph = w.phaseId.reshape(-1)
        flag = np.zeros(ph.size, np.int8)
        indexed0 = (ph != PHASE_NOT_INDEXED) & ~pad

        def unindex(mask, code):
            q[mask] = np.nan
            ph[mask] = PHASE_NOT_INDEXED
            flag[mask] = code

        n_ci = 0
        if p.ci_threshold is not None:
            m = indexed0 & (_arr(w.prop[PROP_NAMES[self.name]["ci"]]).ravel() < p.ci_threshold)
            unindex(m, FLAG_CI)
            n_ci = int(m.sum())

        n_spike = 0
        if p.spike_removal:
            gid, npx, gidx = self._grains_alpha0(w, p.spike_angle_deg)
            m = ~pad & gidx[gid] & (npx[gid] == 1)
            unindex(m, FLAG_SPIKE)
            n_spike = int(m.sum())

        gid, npx, gidx = self._grains_alpha0(w, p.noise_angle_deg)
        noise = ~pad & gidx[gid] & (npx[gid] <= p.noise_max_indexed_px)
        void = ~pad & ~gidx[gid] & (npx[gid] <= p.noise_max_void_px)
        unindex(noise, FLAG_NOISE)
        to_fill = noise | void
        n_filled = n_unfilled = 0
        if p.fill and to_fill.any():
            ok = (ph != PHASE_NOT_INDEXED) & ~pad
            nbr = neighbour_table(_arr(w.x), _arr(w.y), ~pad, hex_grid="hex" in str(w).lower())
            src, _ = dilation_fill(to_fill, gid.copy(), ok, nbr)
            done = to_fill & (src >= 0)
            q[done] = q[src[done]]
            ph[done] = ph[src[done]]
            flag[done] = FLAG_FILLED
            flag[to_fill & (src < 0)] = FLAG_UNFILLED
            n_filled, n_unfilled = int(done.sum()), int((to_fill & (src < 0)).sum())

        n_changed = 0
        if p.hq_filter:
            n_changed = self._hq_filter(w, p, pad)

        w.prop["ti64_cleanup"] = flag.reshape(w.shape)
        rep = CleanupReport(
            n_points=int((~pad).sum()), n_indexed_before=int(indexed0.sum()), n_ci_removed=n_ci,
            n_spikes_removed=n_spike, n_noise_indexed_removed=int(noise.sum()),
            n_small_void_px=int(void.sum()), n_filled=n_filled, n_unfilled=n_unfilled,
            n_indexed_after=int(((ph != PHASE_NOT_INDEXED) & ~pad).sum()),
            n_filtered_changed=n_changed, params=p)
        return self._put(w, self._store[handle.id]["setting"]), rep

    @staticmethod
    def _hq_filter(w, p: CleanupParams, pad) -> int:
        """halfQuadraticFilter on the indexed pixels of each phase (not into not-indexed regions)."""
        from mtex import degree, halfQuadraticFilter  # noqa: PLC0415

        F = halfQuadraticFilter()
        F.alpha = p.hq_alpha
        F.threshold = p.hq_threshold_deg * degree
        before = w.rotations.data.reshape(-1, 4).copy()
        sm = w.smooth(F, fill=False)
        after = np.asarray(sm.rotations.data, float).reshape(-1, 4)
        ok = ~np.isnan(before[:, 0]) & ~np.isnan(after[:, 0]) & ~pad
        w.rotations.data.reshape(-1, 4)[ok] = after[ok]
        dot = np.abs(np.sum(before[ok] * after[ok], axis=1)).clip(0, 1)
        return int(np.sum(np.degrees(2 * np.arccos(dot)) > 0.1))
