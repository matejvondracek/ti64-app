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
from .hexgrid import component_sizes, dilation_fill, neighbour_table
from .pymtex_analysis import PymtexAnalysis

# per-pixel cleanup flags (prop 'ti64_cleanup', same codes in the MATLAB backend)
FLAG_KEPT, FLAG_CI, FLAG_SPIKE, FLAG_NOISE, FLAG_FILLED, FLAG_UNFILLED, FLAG_DILATED = 0, 1, 2, 3, 4, 5, 6


def _arr(v):
    return np.asarray(v() if callable(v) else v)


def _alignment(cs) -> str:
    """'X||a, Y||b*, Z||c' of a pymtex crystal frame (crystalFrame.alignment()); '' for cubic / not indexed."""
    try:
        return ", ".join(str(a) for a in cs.alignment())
    except (AttributeError, TypeError):
        return ""


def _n_regions(mask: np.ndarray, nbr: np.ndarray) -> int:
    """Number of connected regions of a pixel mask (neighbour table)."""
    sizes = component_sizes(mask, nbr)
    m = mask & (sizes > 0)
    return int(np.sum(1.0 / sizes[m]).round()) if m.any() else 0


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

        ebsd = loadEBSD(str(params.file), setting=params.setting)
        # CSList index = file phase id (0 = notIndexed; a single-phase file's phase 0 rows are CSList 1)
        for pid, name in params.phase_names.items():
            if 0 < int(pid) < len(ebsd.CSList):
                ebsd.CSList[int(pid)].mineral = name
        return self._put(ebsd, params.setting)

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
        alignment = {pid: _alignment(cs) for pid, cs in enumerate(ebsd.CSList)
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
                        self._store[handle.id]["setting"], {k: v for k, v in alignment.items() if v})

    def reconstruct(self, handle: EBSDHandle, rp, log_file: str = "") -> tuple[EBSDHandle, dict]:
        from .pymtex_recon import reconstruct, write_log  # noqa: PLC0415

        objs, res = reconstruct(self._get(handle), rp)
        if log_file:
            write_log(log_file, rp, res)
        hid = uuid.uuid4().hex[:12]
        self._store[hid] = {**objs, "recon": True, "setting": self._store[handle.id]["setting"]}
        return EBSDHandle(self.name, hid, handle.n_points), res

    def get_recon(self, handle: EBSDHandle):
        st = self._store.get(handle.id)
        if st is None or not st.get("recon"):
            raise KeyError(f"no pymtex reconstruction {handle.id}")
        return st["reconData"]

    def ipf_colors_quat(self, handle: EBSDHandle, phase: str, quat: np.ndarray, key: str, vec) -> np.ndarray:
        """IPF colours of arbitrary orientations (quaternions a b c d, crystal -> specimen) of one
        phase of a stored map, e.g. parent grain mean orientations; NaN rows stay NaN."""
        from mtex import orientation, quaternion  # noqa: PLC0415

        from .pymtex_plot import make_key  # noqa: PLC0415

        e = self._map_or_grains_ebsd(handle)
        cs = e[phase].CS
        quat = np.asarray(quat, float).reshape(-1, 4)
        out = np.full((quat.shape[0], 3), np.nan)
        ok = ~np.isnan(quat).any(axis=1)
        if ok.any():
            q = quat[ok]
            o = orientation(quaternion(q[:, 0], q[:, 1], q[:, 2], q[:, 3]), cs)
            out[ok] = np.asarray(make_key(cs, key, vec).orientation2color(o), float).reshape(-1, 3)
        return out

    def cell_image(self, x, y, colors, grid, background):
        from ..raster import hexagon_image  # noqa: PLC0415

        return hexagon_image(x, y, colors, grid, background)

    def render_chart(self, spec, out_png):
        from .pymtex_plot import render_chart  # noqa: PLC0415

        return render_chart(spec, out_png)

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
        nbr = neighbour_table(_arr(w.x), _arr(w.y), ~pad, hex_grid="hex" in str(w).lower())
        noise = ~pad & gidx[gid] & (npx[gid] <= p.noise_max_indexed_px)
        # void size: the not-indexed grain's pixel count; not-indexed pixels left without a grain (grain id 0: large
        # regions, pymtex 1.0 / MTEX 7.2) get the size of their connected not-indexed region instead of 0 (fix 4: a
        # raw map with 78 % CI < 0.1 was filled almost entirely as if it were made of 1-px voids)
        ni = ~pad & (ph == PHASE_NOT_INDEXED)
        size = np.where(ni & (gid == 0), component_sizes(ni & (gid == 0), nbr), npx[gid])
        void = ni & ~gidx[gid] & (size <= p.noise_max_void_px)
        unindex(noise, FLAG_NOISE)
        to_fill = noise | void
        n_filled = n_unfilled = 0
        if p.fill and to_fill.any():
            ok = (ph != PHASE_NOT_INDEXED) & ~pad
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

    # -- cleanup as separate operations (fix 5) ---------------------------------------------------------------
    def clean_step(self, handle: EBSDHandle, operation: str, params: dict) -> tuple[EBSDHandle, dict]:
        """ONE cleanup operation on a stored map (base.CLEAN_OPS); the input map stays untouched. Returns the new map
        and a report: n_points, n_indexed_before / after, n_changed and the operation's own counts (removed, filled,
        unfilled + why, per-iteration counts of grain dilation). Same algorithm as matlab/+ti64/cleanStep.m."""
        from .base import clean_params  # noqa: PLC0415

        p = clean_params(operation, params)
        w = self._get(handle).copy()
        pad = _arr(w.isPadding).ravel()
        q = w.rotations.data.reshape(-1, 4)
        ph = w.phaseId.reshape(-1)
        flag = (_arr(w.prop["ti64_cleanup"]).ravel().astype(np.int8).copy() if "ti64_cleanup" in w.prop
                else np.zeros(ph.size, np.int8))
        indexed0 = (ph != PHASE_NOT_INDEXED) & ~pad
        rep: dict = {"operation": operation, "n_points": int((~pad).sum()), "n_indexed_before": int(indexed0.sum())}
        hexg = "hex" in str(w).lower()

        def unindex(mask, code):
            q[mask] = np.nan
            ph[mask] = PHASE_NOT_INDEXED
            flag[mask] = code

        def fill(to_fill, gid, nbr):
            ok = (ph != PHASE_NOT_INDEXED) & ~pad
            src, _ = dilation_fill(to_fill, gid.copy(), ok, nbr)
            done = to_fill & (src >= 0)
            q[done] = q[src[done]]
            ph[done] = ph[src[done]]
            flag[done] = FLAG_FILLED
            flag[to_fill & (src < 0)] = FLAG_UNFILLED
            return int(done.sum()), int((to_fill & (src < 0)).sum())

        if operation == "ci_threshold":
            m = indexed0 & (_arr(w.prop[PROP_NAMES[self.name]["ci"]]).ravel() < p.ci_threshold)
            unindex(m, FLAG_CI)
            rep.update(n_removed=int(m.sum()))
        elif operation == "spike_removal":
            gid, npx, gidx = self._grains_alpha0(w, p.spike_angle_deg)
            m = ~pad & gidx[gid] & (npx[gid] == 1)
            unindex(m, FLAG_SPIKE)
            rep.update(n_removed=int(m.sum()))
        elif operation == "noise_removal":
            gid, npx, gidx = self._grains_alpha0(w, p.noise_angle_deg)
            noise = ~pad & gidx[gid] & (npx[gid] <= p.noise_max_indexed_px)
            unindex(noise, FLAG_NOISE)
            n_f = n_u = 0
            if p.fill and noise.any():
                n_f, n_u = fill(noise, gid, neighbour_table(_arr(w.x), _arr(w.y), ~pad, hex_grid=hexg))
            rep.update(n_removed=int(noise.sum()), n_filled=n_f, n_unfilled=n_u)
        elif operation == "void_fill":
            nbr = neighbour_table(_arr(w.x), _arr(w.y), ~pad, hex_grid=hexg)
            ni = ~pad & (ph == PHASE_NOT_INDEXED)
            size = component_sizes(ni, nbr)               # connected not-indexed region of every not-indexed pixel
            void = ni & (size <= p.max_void_px)
            big = ni & (size > p.max_void_px)
            n_f = n_u = 0
            if void.any():
                gid, _npx, _gidx = self._grains_alpha0(w, p.angle_deg)
                n_f, n_u = fill(void, gid, nbr)
            rep.update(n_not_indexed_before=int(ni.sum()), n_void_px=int(void.sum()), n_filled=n_f, n_unfilled=n_u,
                       n_left_large_void_px=int(big.sum()), n_large_voids=_n_regions(big, nbr))
        elif operation == "grain_dilation":
            rep.update(self._grain_dilation(w, p, pad, q, ph, flag, hexg))
        elif operation == "smoothing":
            cp = CleanupParams(hq_filter=True, hq_alpha=p.hq_alpha, hq_threshold_deg=p.hq_threshold_deg)
            rep.update(n_changed_gt_0_1deg=self._hq_filter(w, cp, pad))
        w.prop["ti64_cleanup"] = flag.reshape(w.shape)
        after = (ph != PHASE_NOT_INDEXED) & ~pad
        rep["n_indexed_after"] = int(after.sum())
        rep["n_not_indexed_after"] = int((~after & ~pad).sum())
        return self._put(w, self._store[handle.id]["setting"]), rep

    def _grain_dilation(self, w, p, pad, q, ph, flag, hexg) -> dict:
        """OIM-style grain dilation (base.GrainDilationParams) with the numba sweep on the neighbour graph."""
        from .hexgrid import dilation_sweeps  # noqa: PLC0415

        gid, npx, gidx = self._grains_alpha0(w, p.tolerance_deg)
        valid_g = gidx & (npx >= p.min_grain_px)
        if p.multi_row:
            y = _arr(w.y).ravel()
            ys = np.unique(np.round(y[~pad], 4))
            dy = float(np.median(np.diff(ys))) if ys.size > 1 else 1.0
            row = np.round((y - ys[0]) / dy).astype(np.int64)
            m = ~pad & (gid > 0)
            rmin = np.full(valid_g.size, np.iinfo(np.int64).max)
            rmax = np.full(valid_g.size, -1)
            np.minimum.at(rmin, gid[m], row[m])
            np.maximum.at(rmax, gid[m], row[m])
            valid_g &= rmax > rmin
        in_grain = ~pad & (ph != PHASE_NOT_INDEXED) & valid_g[gid]
        cand = ~pad & ~in_grain
        nbr = neighbour_table(_arr(w.x), _arr(w.y), ~pad, hex_grid=hexg)
        src, per_iter = dilation_sweeps(cand, gid.copy(), in_grain, nbr, max_iter=1 if p.single_iteration else 100_000)
        done = cand & (src >= 0)
        q[done] = q[src[done]]
        ph[done] = ph[src[done]]
        flag[done] = FLAG_DILATED
        return {"n_candidates": int(cand.sum()), "n_changed": int(done.sum()), "n_unchanged": int((cand & ~done).sum()),
                "n_iterations": len(per_iter), "changed_per_iteration": per_iter,
                "n_grains_kept": int(valid_g[1:].sum())}

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
