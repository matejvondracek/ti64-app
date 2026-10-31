"""pymtex backend: grains, boundaries and per-pixel orientation maps (mixed into PymtexBackend).

Same parameters and outputs as matlab/+ti64/calcGrains.m, grainsExport.m, computeMap.m.
"""
from __future__ import annotations

import uuid

import numpy as np

from .base import PHASE_NOT_INDEXED, GrainParams, GrainsData, GrainsHandle, MapParams, file_order


def _arr(v):
    return np.asarray(v() if callable(v) else v)


def _file_perm(ebsd):
    """(keep mask over the grid, permutation of kept pixels into file order)."""
    keep = ~_arr(ebsd.isPadding).ravel()
    x = _arr(ebsd.x).ravel()[keep]
    y = _arr(ebsd.y).ravel()[keep]
    dy = float(np.median(np.diff(np.unique(np.round(y, 4))))) if y.size > 1 else 1.0
    return keep, file_order(x, y, dy)


def burgers_types(csBeta, csAlpha):
    """The 5 alpha/alpha misorientation types between Burgers variants of one beta orientation
    (10.53, 60, 60.83, 63.26, 90 deg), one representative each, sorted by angle."""
    from mtex import orientation  # noqa: PLC0415

    p2c = orientation.Burgers(csBeta, csAlpha)                  # parent-to-child (CLAUDE.md)
    oc = p2c.variants(orientation.id(csBeta)).reshape(-1)
    mori = oc[0].inv() * oc[1:]
    ang = np.round(np.degrees(_arr(mori.angle())), 2)
    reps = [int(np.flatnonzero(ang == a)[0]) for a in np.unique(ang)]
    return [mori[i] for i in reps], [float(ang[i]) for i in reps]


def gnd_systems(cs, nu: float):
    """Basal, prismatic, pyramidal <a> and 1st-order pyramidal <c+a> (user decision 2026-10-31),
    edge + screw, line energy edge 1, screw 1 - nu (MTEX GND example; Pantleon 2008)."""
    from mtex import dislocationSystem, slipSystem  # noqa: PLC0415

    fams = [slipSystem.basal(cs), slipSystem.prismaticA(cs), slipSystem.pyramidalA(cs), slipSystem.pyramidalCA(cs)]
    # symmetrise each family, then join (classmethod cat): 3 + 3 + 6 + 12 = 24 systems
    sS = slipSystem.cat(*[f.symmetrise(antipodal=True) for f in fams])
    dS = dislocationSystem(sS)            # 24 edge + 9 screw = 33, as MATLAB MTEX 7.2
    u = np.where(_arr(dS.isEdge), 1.0, 1.0 - nu)
    dS.u = u
    return dS


def gnd_map(sub, dS, stencil: str, chunk: int = 250_000) -> np.ndarray:
    """GND density (Pantleon 2008) per pixel of a single-phase map = pymtex calcGND, except that the
    dislocation fit only sees pixels with a fully known in-plane curvature.
    Workaround (docs/decisions.md D9): pymtex 1.0.0 fitDislocationSystems takes the set of known
    curvature components from the FIRST pixel; if that pixel is NaN (e.g. next to a grain boundary)
    every pixel gets rho = 0. MATLAB MTEX 7.2 has no such problem."""
    from mtex import fitDislocationSystems  # noqa: PLC0415
    from mtex.tensors.tensor import curvatureTensor  # noqa: PLC0415

    kappa = sub.curvature(stencil=stencil)
    M = np.asarray(kappa.M, float).reshape(-1, 3, 3)
    plane = ~np.isnan(M).all(axis=(0, 1))                 # columns the map plane knows
    valid = ~np.isnan(M[:, :, plane]).any(axis=(1, 2))
    out = np.full(M.shape[0], np.nan)
    if not valid.any():
        return out
    # in chunks: the systems rotated into every pixel take ~1.6 kB per pixel (2.6 M px -> 4 GB at once)
    ori = sub.orientations.reshape(-1, 1)
    vi = np.flatnonzero(valid)
    for a in range(0, vi.size, chunk):
        sel = vi[a:a + chunk]
        kv = curvatureTensor(M[sel], kappa.frame, unit=kappa.unit)
        dSRot = ori[sel] * dS
        rho, factor = fitDislocationSystems(kv, dSRot)
        u = np.broadcast_to(np.asarray(dSRot.u, float), np.shape(rho))
        out[sel] = factor * np.sum(np.abs(np.asarray(rho) * u), axis=1)
    return out


class PymtexAnalysis:
    """Mixin: needs self._store, self._get, self._put_obj."""

    # -- grains -------------------------------------------------------------------------------
    def calc_grains(self, handle, gp: GrainParams) -> GrainsHandle:
        from mtex import calcGrains, degree  # noqa: PLC0415

        e = self._get(handle).copy()
        work = e.copy()
        g = calcGrains(work, angle=[gp.hagb_deg * degree, gp.lagb_deg * degree], alpha=gp.alpha,
                       minPixel=gp.min_px)
        # calcGrains also marks not-indexed pixels within 'alpha' of a grain as indexed (NaN
        # orientation), as MTEX 7 does: keep the measured map, attach only the grain ids (decisions D7)
        e.prop["grainId"] = work.prop["grainId"]
        gid = uuid.uuid4().hex[:12]
        self._store[gid] = {"grains": g, "ebsd": e, "params": gp, "source": handle.id}
        return GrainsHandle(self.name, gid, int(len(_arr(g.id))))

    def _grains(self, gh):
        if gh.backend != self.name or gh.id not in self._store or "grains" not in self._store[gh.id]:
            raise KeyError(f"no pymtex grains {gh.id}")
        return self._store[gh.id]

    def get_grains(self, gh: GrainsHandle) -> GrainsData:
        st = self._grains(gh)
        e, g = st["ebsd"], st["grains"]
        keep, perm = _file_perm(e)
        pgid = _arr(e.prop["grainId"]).ravel()[keep][perm].astype(np.int64)
        area = _arr(g.area).ravel().astype(float)
        phase = _arr(g.phaseId).ravel().astype(np.int16)
        mq = np.full((phase.size, 4), np.nan)
        for pid, cs in enumerate(e.CSList):    # pymtex: mean orientations one phase at a time
            if pid == PHASE_NOT_INDEXED or not (phase == pid).any():
                continue
            mq[phase == pid] = np.array(g[cs.mineral].meanOrientation.data, float).reshape(-1, 4)
        grain = {"id": _arr(g.id).ravel().astype(np.int64), "phase": phase,
                 "n_px": _arr(g.numPixel).ravel().astype(np.int64), "area_um2": area,
                 "ecd_um": 2 * np.sqrt(area / np.pi), "is_edge": _arr(g.isBoundary).ravel().astype(bool),
                 "mean_quat": mq}
        names = {pid: cs.mineral for pid, cs in enumerate(e.CSList) if pid != PHASE_NOT_INDEXED}
        csA = next((cs for pid, cs in enumerate(e.CSList) if pid and "alpha" in str(cs.mineral)), None)
        csB = next((cs for pid, cs in enumerate(e.CSList) if pid and "beta" in str(cs.mineral)), None)
        types = burgers_types(csB, csA)[0] if csA is not None and csB is not None else []
        parts = [self._boundary_table(g.boundary, False, names, types, csA),
                 self._boundary_table(g.innerBoundary, True, names, types, csA)]
        boundary = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        return GrainsData(pgid, grain, boundary)

    @staticmethod
    def _boundary_table(gb, inner: bool, names: dict, types: list, csA) -> dict[str, np.ndarray]:
        n = len(gb)
        V = _arr(gb.V)
        F = _arr(gb.F).astype(np.int64)
        gid = _arr(gb.grainId).astype(np.int64).reshape(n, 2)
        pid = _arr(gb.phaseId).astype(np.int64).reshape(n, 2)
        mis = np.full(n, np.nan)
        btype = np.zeros(n, np.int8)
        bdist = np.full(n, np.nan)
        for k, name in names.items():
            m = (pid[:, 0] == k) & (pid[:, 1] == k) & (gid > 0).all(axis=1)
            if not m.any():
                continue
            sub = gb[m]
            mo = sub.misorientation
            mis[m] = np.degrees(_arr(mo.angle()))
            if types and csA is not None and name == csA.mineral:
                d = np.stack([np.degrees(_arr(mo.angle(t))) for t in types], axis=1)
                btype[m] = np.argmin(d, axis=1) + 1
                bdist[m] = d.min(axis=1)
        return {"x1": V[F[:, 0], 0], "y1": V[F[:, 0], 1], "x2": V[F[:, 1], 0], "y2": V[F[:, 1], 1],
                "length_um": _arr(gb.segLength).astype(float).reshape(n),
                "phase1": np.maximum(pid[:, 0], 0).astype(np.int16),   # outside the map: 0 (as MATLAB)
                "phase2": np.maximum(pid[:, 1], 0).astype(np.int16),
                "grain1": gid[:, 0], "grain2": gid[:, 1], "mis_deg": mis,
                "burgers_type": btype, "burgers_dist_deg": bdist, "inner": np.full(n, inner)}

    # -- per-pixel maps -----------------------------------------------------------------------
    def compute_map(self, gh: GrainsHandle, mp: MapParams) -> np.ndarray:
        """Per-pixel values in FILE order (deg; GND in 1/m^2), NaN where undefined."""
        from mtex import degree  # noqa: PLC0415

        st = self._grains(gh)
        e, g = st["ebsd"], st["grains"]
        keep, perm = _file_perm(e)
        ph = _arr(e.phaseId).ravel()
        out = np.full(ph.size, np.nan)
        if mp.kind == "kam":
            v = _arr(e.KAM(order=mp.kam_order, threshold=mp.kam_threshold_deg * degree)).ravel()
            out = np.degrees(v)
            out[ph == PHASE_NOT_INDEXED] = np.nan
        elif mp.kind in ("grod", "gos"):
            for pid, cs in enumerate(e.CSList):
                if pid == PHASE_NOT_INDEXED or not (ph == pid).any():
                    continue
                sub = e[cs.mineral]
                grod = sub.calcGROD(g)
                ang = _arr(grod.angle()) if hasattr(grod, "angle") else _arr(grod)
                out[self._sub_index(e, ph, pid, sub)] = np.degrees(ang.ravel()[:int((ph == pid).sum())]) \
                    if ang.size != ph.size else np.degrees(ang.ravel())[ph == pid]
            if mp.kind == "gos":
                gid = _arr(e.prop["grainId"]).ravel().astype(np.int64)
                ok = ~np.isnan(out) & (gid > 0)
                s = np.bincount(gid[ok], weights=out[ok], minlength=gid.max() + 1)
                c = np.bincount(gid[ok], minlength=gid.max() + 1)
                mean = np.divide(s, c, out=np.full(s.shape, np.nan), where=c > 0)
                out = np.where(gid > 0, mean[gid], np.nan)
                out[ph == PHASE_NOT_INDEXED] = np.nan
        elif mp.kind == "gnd":
            name = mp.gnd_phase
            pid = e.CSList.phaseId(name)
            sub = e[name]
            gnd = gnd_map(sub, gnd_systems(sub.CS, mp.poisson), mp.gnd_stencil)
            if gnd.size == ph.size:
                out = np.where(ph == pid, gnd, np.nan)
            else:
                out[self._sub_index(e, ph, pid, sub)] = gnd
        return out[keep][perm]

    # -- IPF colours and figures --------------------------------------------------------------
    def _map_or_grains_ebsd(self, handle):
        st = self._store.get(handle.id)
        if st is None:
            raise KeyError(f"no pymtex object {handle.id}")
        return st["ebsd"]

    def ipf_colors(self, handle, phase: str, key: str, vec) -> np.ndarray:
        """(n, 3) IPF colours of one phase in FILE order, NaN for other pixels."""
        from .pymtex_plot import make_key  # noqa: PLC0415

        e = self._map_or_grains_ebsd(handle)
        keep, perm = _file_perm(e)
        ph = _arr(e.phaseId).ravel()
        pid = e.CSList.phaseId(phase)
        out = np.full((ph.size, 3), np.nan)
        if (ph == pid).any():
            sub = e[phase]
            col = np.asarray(make_key(sub.CS, key, vec).orientation2color(sub.orientations), float).reshape(-1, 3)
            out[np.flatnonzero(ph == pid)] = col
        return out[keep][perm]

    def render(self, handle, spec, out_png):
        from .pymtex_plot import render  # noqa: PLC0415

        e = self._map_or_grains_ebsd(handle)
        keep, perm = _file_perm(e)
        return render(e, spec, out_png, keep, perm)

    def render_key(self, handle, phase: str, key: str, vec, out_png, dpi: int = 300):
        from .pymtex_plot import render_key  # noqa: PLC0415

        e = self._map_or_grains_ebsd(handle)
        return render_key(e.CSList[e.CSList.phaseId(phase)] if hasattr(e.CSList, "__getitem__") else e[phase].CS,
                          key, vec, out_png, dpi)

    @staticmethod
    def _sub_index(e, ph, pid, sub):
        """Flat grid indices of a phase sub-map's pixels (sub keeps the grid order of its phase)."""
        return np.flatnonzero(ph == pid)
