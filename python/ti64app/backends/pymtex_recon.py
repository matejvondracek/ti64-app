"""pymtex port of the prior-beta reconstruction (EBSD_part2.m / docs/pymtex_recon_test.py).
Same steps, parameters and metrics as matlab/+ti64/reconstruct.m (ReconParams in base.py).
Methods: Niessen et al. 2022 doi:10.1107/S1600576721011560; variant graph Hielscher et al. 2022
doi:10.1016/j.mtla.2022.101399; thresholds Yang et al. 2025 doi:10.1016/j.ijlmm.2025.07.009.
"""
from __future__ import annotations

import time

import numpy as np


def _a(v):
    return np.asarray(v() if callable(v) else v)


def _merge(job, par, mode: str, thr_deg: float):
    from mtex import degree  # noqa: PLC0415

    if thr_deg <= 0:
        return
    if mode == "progressive":
        steps = np.unique(np.append(np.arange(par.mergeStart, thr_deg + 1e-9, par.mergeStep), thr_deg))
        steps = steps[steps <= thr_deg + 1e-9]
    else:
        steps = [thr_deg]
    for st in steps:
        job.mergeSimilar(threshold=float(st) * degree)


def _metrics(job, par, mergeIdPre, gidPairs, misPre, segLen):
    """[grains >= histMinECD, area-weighted ECD, bridged steps %, split noise %] (EBSD_part2 mergeMetrics):
    pre2final maps each pre-merge grain id to its final grain via the prior (child) grains."""
    pg = job.parentGrains
    A = _a(pg.area).ravel().astype(float)
    E = 2 * np.sqrt(A / np.pi)
    mid = _a(job.mergeId).ravel().astype(np.int64)
    pre = np.asarray(mergeIdPre).ravel().astype(np.int64)
    pre2final = np.zeros(max(pre.max(), gidPairs.max()) + 1, np.int64)
    pre2final[pre] = mid
    inside = pre2final[gidPairs[:, 0]] == pre2final[gidPairs[:, 1]]
    step = misPre > par.stepAngle
    noise = misPre < par.noiseAngle
    return [int((E >= par.histMinECD).sum()), float((E * A).sum() / A.sum()),
            100 * float(segLen[step & inside].sum() / max(segLen[step].sum(), 1e-12)),
            100 * float(segLen[noise & ~inside].sum() / max(segLen[noise].sum(), 1e-12))]


def _phase_cs(ebsd, mineral: str):
    """Crystal symmetry of a phase from the map's phase list (declared in the .ang header even if no pixel
    is indexed, or all were removed as noise); beta fallback as EBSD_part2.m: m-3m, a = 3.32 A."""
    from mtex import crystalFrame  # noqa: PLC0415

    try:
        return ebsd.CSList[ebsd.CSList.phaseId(mineral)]
    except Exception:  # noqa: BLE001
        if "beta" in mineral:
            return crystalFrame("m-3m", [3.32, 3.32, 3.32], mineral=mineral)
        raise ValueError(f"phase {mineral!r} is not in the map") from None


def reconstruct(ebsdRaw, par) -> tuple[dict, dict]:
    """par: ReconParams (attributes as in reconstruct.m). Returns (objects, results)."""
    from mtex import calcGrains, degree, orientation, parentGrainReconstructor  # noqa: PLC0415

    t_all = time.time()
    res: dict = {}
    # noise cleanup on ALL pixels, alpha = 0 (as the MATLAB port)
    e1 = ebsdRaw.copy()
    g1 = calcGrains(e1, angle=par.cleanAngle * degree, alpha=0)
    gid1 = _a(e1.prop["grainId"]).ravel().astype(np.int64)
    ids = _a(g1.id).ravel().astype(np.int64)
    npx = _a(g1.numPixel).ravel()
    idx = _a(g1.isIndexed).ravel().astype(bool)
    noise_ids = ids[(idx & (npx <= par.noiseMaxIndexed)) | (~idx & (npx <= par.noiseMaxVoid))]
    pad = _a(ebsdRaw.isPadding).ravel()
    keep = ~np.isin(gid1, noise_ids) & ~pad
    res["n_noise_px"] = int((~keep & ~pad).sum())
    ebsd = ebsdRaw.flatten()[keep]
    grains = calcGrains(ebsd, angle=par.segAngle * degree)
    if par.smoothIter > 0:
        grains = grains.smoothBoundary(par.smoothIter) if hasattr(grains, "smoothBoundary") else grains
    res["n_child_grains"] = int(len(_a(grains.id)))

    job = parentGrainReconstructor(ebsd, grains)
    job.csParent = _phase_cs(ebsd, "Ti (beta)")
    job.csChild = _phase_cs(ebsd, "Ti (alpha)")
    p2cB = orientation.Burgers(job.csParent, job.csChild)
    job.p2c = p2cB
    res["or_dev_deg"], res["or_used"] = float("nan"), "burgers"
    if par.orRefine:
        job.calcParent2Child()
        dev = float(np.degrees(_a(job.p2c.angle(p2cB))).ravel()[0])
        res["or_dev_deg"] = dev
        use = par.orMode == "refined" or (par.orMode == "limit" and dev <= par.orMaxDev)
        if use:
            res["or_used"] = "refined"
        else:
            job.p2c = p2cB

    if par.method == "variantGraph":
        job.calcVariantGraph(threshold=par.vgThreshold * degree, tol=par.vgTolerance * degree)
        job.clusterVariantGraph(numIter=par.vgNumIter, inflationPower=par.vgInflation)
        job.calcParentFromVote(minProb=par.minProb)
    else:
        job.calcGraph(threshold=par.graphThreshold * degree, tol=par.graphTolerance * degree)
        job.clusterGraph(inflationPower=par.graphInflation)
        job.calcParentFromGraph()
        job.calcGBVotes(p2c=True, threshold=par.graphThreshold * degree, tol=par.graphTolerance * degree)
        job.calcParentFromVote(minProb=par.minProb)
    grainsPre, mergeIdPre = job.grains, np.array(_a(job.mergeId)).copy()

    # neighbour misorientation before merging (between parent orientations of neighbouring grains)
    pid = job.parentPhaseId
    gPre = grainsPre[_a(grainsPre.phaseId).ravel() == pid]
    name = job.csParent.mineral
    gB = gPre.boundary[name, name]
    gidPairs = _a(gB.grainId).astype(np.int64).reshape(-1, 2)
    pairInd = np.stack([_a(gPre.id2ind(gidPairs[:, 0])).ravel(), _a(gPre.id2ind(gidPairs[:, 1])).ravel()], 1)
    mo = gPre.meanOrientation
    misPre = np.degrees(_a(mo[pairInd[:, 0]].angle(mo[pairInd[:, 1]]))).ravel()
    segLen = _a(gB.segLength).ravel().astype(float)

    sweep = []
    for mode, thr in par.sweep:
        job.grains, job.mergeId = grainsPre, mergeIdPre.copy()
        _merge(job, par, mode, thr)
        job.mergeInclusions(maxSize=par.inclusionMaxSize)
        sweep.append(_metrics(job, par, mergeIdPre, gidPairs, misPre, segLen))
    res["sweep"] = sweep
    job.grains, job.mergeId = grainsPre, mergeIdPre.copy()
    _merge(job, par, par.mergeMode, par.mergeThreshold)
    job.mergeInclusions(maxSize=par.inclusionMaxSize)
    fin = _metrics(job, par, mergeIdPre, gidPairs, misPre, segLen)

    pg = job.parentGrains
    pe = job.calcParentEBSD()
    fit = np.degrees(_a(pe.prop["fit"]).ravel()) if "fit" in pe.prop else np.degrees(_a(pe.fit).ravel())
    fit = fit[~np.isnan(fit)]
    A = _a(pg.area).ravel().astype(float)
    E = 2 * np.sqrt(A / np.pi)
    ca = _a(job.grainsPrior.area).ravel()
    isC = _a(job.grainsPrior.phaseId).ravel() == job.childPhaseId
    res.update(n_grains_min_ecd=int((E >= par.histMinECD).sum()), n_grains_all=int(E.size),
               rec_frac=float(ca[_a(job.isTransformed).ravel()].sum() / ca[isC].sum()),
               mean_fit_deg=float(fit.mean()), bad_fit_frac=float((fit > par.badFit).mean()),
               ecd_aw_um=float((E * A).sum() / A.sum()), bridged_pct=fin[2], split_pct=fin[3],
               fit_quantiles=np.quantile(fit, [0.1, 0.25, 0.5, 0.75, 0.9]).tolist(),
               mis_share=(np.histogram(misPre, [0, .5, 1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10, 15, np.inf],
                                       weights=segLen)[0] / segLen.sum()).tolist(),
               minutes=(time.time() - t_all) / 60)
    pmap = parent_map(ebsdRaw, keep, pe)
    rdata = recon_data(pg, gB, misPre)
    objs = {"job": job, "parentGrains": pg, "parentEBSD": pe, "ebsdKept": ebsd, "childGrains": grains,
            "ebsd": pmap, "reconData": rdata}
    return objs, res


def parent_map(ebsdRaw, keep, pe):
    """The loaded map (same grid) with the parent EBSD put back on the kept pixels; removed noise
    = not indexed. Props ti64_parent_grain, ti64_recon_fit (deg), ti64_recon_state (base.py codes)."""
    from .base import (RECON_NOT_INDEXED, RECON_REMOVED, RECON_RETAINED_BETA, RECON_TRANSFORMED,  # noqa: PLC0415
                       RECON_UNTRANSFORMED)

    P = ebsdRaw.copy()
    pad = _a(P.isPadding).ravel()
    q = P.rotations.data.reshape(-1, 4)
    ph = P.phaseId.reshape(-1)
    ph0 = ph.copy()
    kidx = np.flatnonzero(keep)
    pid_beta = P.CSList.phaseId("Ti (beta)")
    removed = ~keep & ~pad & (ph0 != 0)
    q[removed] = np.nan
    ph[removed] = 0
    q[kidx] = np.asarray(pe.rotations.data, float).reshape(-1, 4)
    ph[kidx] = _a(pe.phaseId).ravel()
    n = ph.size
    pg_id = np.zeros(n, np.int64)
    pg_id[kidx] = _a(pe.prop["grainId"]).ravel().astype(np.int64)
    fit = np.full(n, np.nan)
    fit[kidx] = np.degrees(_a(pe.prop["fit"]).ravel())
    state = np.full(n, RECON_NOT_INDEXED, np.int8)
    state[removed] = RECON_REMOVED
    child = (ph0 != 0) & (ph0 != pid_beta) & ~removed & ~pad
    state[child & (ph == pid_beta)] = RECON_TRANSFORMED
    state[child & (ph != pid_beta) & (ph != 0)] = RECON_UNTRANSFORMED
    state[(ph0 == pid_beta) & ~removed & ~pad] = RECON_RETAINED_BETA
    void = (state == RECON_NOT_INDEXED) & ~pad          # calcGrains may index voids inside grains (D7)
    ph[void] = 0
    q[void] = np.nan
    fit[state != RECON_TRANSFORMED] = np.nan
    pg_id[(ph != pid_beta)] = 0
    for name, v in (("ti64_parent_grain", pg_id), ("ti64_recon_fit", fit), ("ti64_recon_state", state)):
        P.prop[name] = v.reshape(P.shape)
    return P


def _segments(gb) -> dict:
    V = _a(gb.V)
    F = _a(gb.F).astype(np.int64)
    return {"x1": V[F[:, 0], 0], "y1": V[F[:, 0], 1], "x2": V[F[:, 1], 0], "y2": V[F[:, 1], 1],
            "length_um": _a(gb.segLength).astype(float).ravel()}


def recon_data(pg, gBpre, misPre):
    """ReconData (base.py) of the parent grains + pre-merge beta/beta boundaries."""
    from .base import ReconData  # noqa: PLC0415

    ids = _a(pg.id).ravel().astype(np.int64)
    area = _a(pg.area).ravel().astype(float)
    mq = np.array(pg.meanOrientation.data, float).reshape(-1, 4)
    grain = {"id": ids, "n_px": _a(pg.numPixel).ravel().astype(np.int64), "area_um2": area,
             "ecd_um": 2 * np.sqrt(area / np.pi), "is_edge": _a(pg.isBoundary).ravel().astype(bool),
             "mean_quat": mq}
    gb = pg.boundary
    seg = _segments(gb)
    gid = _a(gb.grainId).astype(np.int64).reshape(-1, 2)
    lut = np.full(max(int(gid.max(initial=0)), int(ids.max(initial=0))) + 1, -1, np.int64)
    lut[ids] = np.arange(ids.size)
    ind = np.where(gid > 0, lut[np.maximum(gid, 0)], -1)
    both = (ind >= 0).all(axis=1)
    mis = np.full(len(gid), np.nan)
    if both.any():
        mo = pg.meanOrientation
        a, b = ind[both, 0], ind[both, 1]
        mis[both] = np.degrees(_a(mo[a].angle(mo[b]))).ravel()
    boundary = {**seg, "grain1": np.where(ind[:, 0] >= 0, gid[:, 0], 0),
                "grain2": np.where(ind[:, 1] >= 0, gid[:, 1], 0), "mis_deg": mis}
    pre = {**_segments(gBpre), "mis_deg": np.asarray(misPre, float).ravel()}
    return ReconData(grain, boundary, pre)


def write_log(path, par, res) -> None:
    """One line per run, same format as EBSD_part2.m reconstruction_log.txt."""
    from datetime import datetime  # noqa: PLC0415

    line = (f"{datetime.now():%Y-%m-%d %H:%M} | {par.method} seg {par.segAngle:.1f} vgThr {par.vgThreshold:.1f} "
            f"vgTol {par.vgTolerance:.1f} iter {par.vgNumIter:d} infl {par.vgInflation:.2f} | gThr "
            f"{par.graphThreshold:.1f} gTol {par.graphTolerance:.1f} gInfl {par.graphInflation:.2f} | minP "
            f"{par.minProb:.2f} merge {par.mergeMode} {par.mergeThreshold:.1f} incl {par.inclusionMaxSize:d} | "
            f"grains>={par.histMinECD:g}um {res['n_grains_min_ecd']} recFrac {100 * res['rec_frac']:.1f}% "
            f"meanFit {res['mean_fit_deg']:.2f} badFit {100 * res['bad_fit_frac']:.1f}% ECDaw "
            f"{res['ecd_aw_um']:.1f} | {res['minutes']:.1f} min\n")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
