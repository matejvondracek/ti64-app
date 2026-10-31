"""Python-only analysis of a prior-beta reconstruction (consumes backend results, never calls a backend).

- Ti variant / packet IDs per alpha pixel from the child (alpha) and parent (beta) orientations,
  numbered against the 12 IDEAL Burgers variants (docs/decisions.md D13):
    Burgers OR (110)beta || (0001)alpha, [1-11]beta || [2-1-10]alpha (Burgers 1934; Ti packets as in
    e.g. Stanford & Bate 2004 doi:10.1016/j.actamat.2004.08.034 -- packet = alpha sharing one {110}beta
    plane, NOT MTEX's steel packet convention {111}_p || {110}_c).
    packet p = 1..6: which of the 6 {110}beta planes (list BCC110) is parallel to the alpha c-axis;
    variant v = 2 (p - 1) + j, j = 1, 2: which of the two <111>beta in that plane (BCC111_IN_110[p])
    is parallel to one of the alpha <11-20>. Deviations of both are reported (QA).
  The same numbering is used for both backends, so variant statistics are comparable.
- Variant-selection statistics per prior-beta grain and per map.
- Prior-beta grain size (ECD) and caliper lengths along / across the build direction
  (ISO 13067 / ASTM E2627: minimum pixels per grain, edge grains excluded, method reported).

Quaternions: (a, b, c, d), MTEX convention orientation = crystal -> specimen (base.EBSDData).
Hexagonal crystal frame X || a, Y || b*, Z || c (checked in pymtex 1.0.0 and MTEX 7.2 for the test maps).
"""
from __future__ import annotations

import numpy as np

S3 = np.sqrt(3.0)
HEX_C = np.array([0.0, 0.0, 1.0])
HEX_A = np.array([[1.0, 0.0, 0.0], [-0.5, S3 / 2, 0.0], [-0.5, -S3 / 2, 0.0]])     # <2-1-10>, X || a
BCC110 = np.array([[1, 1, 0], [1, -1, 0], [1, 0, 1], [1, 0, -1], [0, 1, 1], [0, 1, -1]], float) / np.sqrt(2)
_111 = np.array([[1, 1, 1], [1, 1, -1], [1, -1, 1], [-1, 1, 1]], float) / S3
# the two <111> lying in each {110} plane (perpendicular to its normal), in a fixed order
BCC111_IN_110 = np.array([[d for d in _111 if abs(d @ n) < 1e-9] for n in BCC110])     # (6, 2, 3)


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    """(n, 4) unit quaternions a b c d -> (n, 3, 3) rotation matrices (v_specimen = R v_crystal)."""
    q = np.asarray(q, float)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    a, b, c, d = q.T
    return np.stack([
        np.stack([a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)], -1),
        np.stack([2 * (b * c + a * d), a * a - b * b + c * c - d * d, 2 * (c * d - a * b)], -1),
        np.stack([2 * (b * d - a * c), 2 * (c * d + a * b), a * a - b * b - c * c + d * d], -1)], 1)


def ti_variants(child_quat: np.ndarray, parent_quat: np.ndarray, chunk: int = 500_000) -> dict[str, np.ndarray]:
    """Per pixel: packet (1..6), variant (1..12), c_dev_deg (alpha c-axis to the {110}beta normal),
    a_dev_deg (nearest alpha <11-20> to the chosen <111>beta). 0 / NaN where a quaternion is NaN."""
    n = child_quat.shape[0]
    packet = np.zeros(n, np.int8)
    variant = np.zeros(n, np.int8)
    cdev = np.full(n, np.nan)
    adev = np.full(n, np.nan)
    ok = ~np.isnan(child_quat).any(axis=1) & ~np.isnan(parent_quat).any(axis=1)
    idx = np.flatnonzero(ok)
    for s in range(0, idx.size, chunk):
        sel = idx[s:s + chunk]
        M = np.einsum("nji,njk->nik", quat_to_matrix(parent_quat[sel]), quat_to_matrix(child_quat[sel]))
        c = M @ HEX_C                                       # alpha c-axis in the parent crystal frame
        a = np.einsum("nij,kj->nki", M, HEX_A)              # (m, 3, 3) alpha a-axes in parent frame
        dc = np.abs(c @ BCC110.T)                           # (m, 6)
        p = np.argmax(dc, axis=1)
        cdev[sel] = np.degrees(np.arccos(np.clip(dc[np.arange(sel.size), p], 0, 1)))
        d111 = BCC111_IN_110[p]                             # (m, 2, 3)
        da = np.abs(np.einsum("mjx,mkx->mjk", d111, a)).max(axis=2)   # (m, 2) best a-axis per <111>
        j = np.argmax(da, axis=1)
        adev[sel] = np.degrees(np.arccos(np.clip(da[np.arange(sel.size), j], 0, 1)))
        packet[sel] = p + 1
        variant[sel] = 2 * p + j + 1
    return {"packet": packet, "variant": variant, "c_dev_deg": cdev, "a_dev_deg": adev}


def _cubic_ops() -> np.ndarray:
    """The 24 proper cubic rotations as quaternions a b c d."""
    from scipy.spatial.transform import Rotation  # noqa: PLC0415

    x, y, z, w = Rotation.create_group("O").as_quat().T
    return np.stack([w, x, y, z], 1)


def _qmul(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    a1, b1, c1, d1 = np.moveaxis(p, -1, 0)
    a2, b2, c2, d2 = np.moveaxis(q, -1, 0)
    return np.stack([a1 * a2 - b1 * b2 - c1 * c2 - d1 * d2, a1 * b2 + b1 * a2 + c1 * d2 - d1 * c2,
                     a1 * c2 - b1 * d2 + c1 * a2 + d1 * b2, a1 * d2 + b1 * c2 - c1 * b2 + d1 * a2], -1)


def canonical_cubic(q: np.ndarray) -> np.ndarray:
    """Symmetric equivalent q * s (s = cubic crystal symmetry) with the largest |scalar part|, sign a >= 0:
    a deterministic representative, so variant IDs do not depend on which equivalent a backend returns."""
    q = np.asarray(q, float).reshape(-1, 4)
    eq = _qmul(q[:, None, :], _cubic_ops()[None, :, :])          # (n, 24, 4)
    k = np.nanargmax(np.abs(eq[..., 0]), axis=1) if len(q) else np.zeros(0, int)
    out = eq[np.arange(len(q)), k]
    out *= np.where(out[:, :1] < 0, -1.0, 1.0)
    out[np.isnan(q).any(axis=1)] = np.nan
    return out


def grain_mean_parent(parent_grain: np.ndarray, grain: dict) -> np.ndarray:
    """(n, 4) mean parent orientation of each pixel's prior-beta grain (NaN if none), as the canonical cubic
    equivalent. Variant IDs must be counted against ONE representative parent orientation per grain: the
    per-pixel parent orientation of MTEX's calcParentEBSD is the symmetric equivalent that makes the child
    variant 1 (always); the canonical choice makes the IDs the same in both backends (except for grains whose
    mean lies within ~1 deg of a tie between two equivalents)."""
    ids = np.asarray(grain["id"], np.int64)
    pg = np.asarray(parent_grain, np.int64)
    lut = np.full(max(int(pg.max(initial=0)), int(ids.max(initial=0))) + 1, -1, np.int64)
    lut[ids] = np.arange(ids.size)
    out = np.full((pg.size, 4), np.nan)
    ok = pg > 0
    ok[ok] = lut[pg[ok]] >= 0
    mq = canonical_cubic(np.asarray(grain["mean_quat"], float))
    out[ok] = mq[lut[pg[ok]]]
    return out


def variant_stats(parent_grain: np.ndarray, variant: np.ndarray, packet: np.ndarray, fit_deg: np.ndarray,
                  min_share: float = 0.01) -> dict:
    """Variant selection: per map (pixel share and mean fit per variant ID, EBSD_part2 summary) and per
    prior-beta grain (number of variants / packets above min_share of the grain, share of the largest
    variant). Variant IDs are relative to each grain's parent orientation, so the per-grain numbers are
    the meaningful ones."""
    m = (variant > 0) & (parent_grain > 0)
    v, g, pk, f = variant[m].astype(np.int64), parent_grain[m].astype(np.int64), packet[m].astype(np.int64), fit_deg[m]
    tot = max(int(m.sum()), 1)
    share = np.bincount(v, minlength=13)[1:] / tot
    fok = ~np.isnan(f)
    fsum = np.bincount(v[fok], weights=f[fok], minlength=13)[1:]
    fcnt = np.bincount(v[fok], minlength=13)[1:]
    mean_fit = np.divide(fsum, fcnt, out=np.full(12, np.nan), where=fcnt > 0)
    out = {"n_pixels": int(m.sum()), "variant_share": share.tolist(), "variant_mean_fit_deg": mean_fit.tolist(),
           "packet_share": (np.bincount(pk, minlength=7)[1:] / tot).tolist(), "min_share": min_share}
    if not m.any():
        out.update(grains=[], n_variants_hist=[0] * 13, n_packets_hist=[0] * 7)
        return out
    ug, gi = np.unique(g, return_inverse=True)
    cv = np.zeros((ug.size, 12))
    np.add.at(cv, (gi, v - 1), 1)
    cp = np.zeros((ug.size, 6))
    np.add.at(cp, (gi, pk - 1), 1)
    npx = cv.sum(1)
    fv, fp = cv / npx[:, None], cp / npx[:, None]
    nv = (fv >= min_share).sum(1)
    npk = (fp >= min_share).sum(1)
    out["grains"] = [{"grain": int(ug[i]), "n_px": int(npx[i]), "n_variants": int(nv[i]),
                      "n_packets": int(npk[i]), "largest_variant_share": float(fv[i].max())}
                     for i in range(ug.size)]
    out["n_variants_hist"] = np.bincount(nv, minlength=13).tolist()      # index = number of variants
    out["n_packets_hist"] = np.bincount(npk, minlength=7).tolist()
    return out


def bd_in_plane(bd: tuple[float, float, float]) -> tuple[np.ndarray | None, float]:
    """Unit in-plane build direction (x, y) and the angle of BD out of the map plane (deg)."""
    v = np.asarray(bd, float)
    v = v / np.linalg.norm(v)
    ip = np.hypot(v[0], v[1])
    out_deg = float(np.degrees(np.arccos(np.clip(ip, 0, 1))))
    return (v[:2] / ip if ip > 1e-6 else None), out_deg


def grain_shape(x: np.ndarray, y: np.ndarray, parent_grain: np.ndarray, grain: dict, bd,
                step_um: float, min_px: int, min_ecd_um: float, max_bd_out_of_plane_deg: float = 20.0) -> dict:
    """Per prior-beta grain: ECD and caliper (Feret) lengths along / across the in-plane build direction,
    computed from pixel centres (max - min of the projection) + one step (pixel width).
    Statistics only over grains with >= min_px pixels, ECD >= min_ecd_um and NOT touching the map edge
    (ISO 13067 / ASTM E2627). If BD is more than max_bd_out_of_plane_deg out of the map plane, the
    calipers are not defined (None) and a warning is returned."""
    ids = np.asarray(grain["id"], np.int64)
    u, out_deg = bd_in_plane(bd)
    warnings = []
    if out_deg > max_bd_out_of_plane_deg:
        warnings.append(f"build direction is {out_deg:.0f} deg out of the map plane: calipers along/across "
                        "BD not computed")
        u = None
    n = ids.size
    along = np.full(n, np.nan)
    across = np.full(n, np.nan)
    if u is not None:
        m = parent_grain > 0
        g = parent_grain[m].astype(np.int64)
        pa = x[m] * u[0] + y[m] * u[1]
        pc = -x[m] * u[1] + y[m] * u[0]
        order = np.argsort(g, kind="stable")
        gs, pa, pc = g[order], pa[order], pc[order]
        starts = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1]])
        gid = gs[starts]
        la = np.maximum.reduceat(pa, starts) - np.minimum.reduceat(pa, starts) + step_um
        lc = np.maximum.reduceat(pc, starts) - np.minimum.reduceat(pc, starts) + step_um
        lut = {int(k): i for i, k in enumerate(ids)}
        pos = np.array([lut.get(int(k), -1) for k in gid])
        ok = pos >= 0
        along[pos[ok]] = la[ok]
        across[pos[ok]] = lc[ok]
    ecd = np.asarray(grain["ecd_um"], float)
    area = np.asarray(grain["area_um2"], float)
    used = (np.asarray(grain["n_px"]) >= min_px) & (ecd >= min_ecd_um) & ~np.asarray(grain["is_edge"], bool)
    aspect = along / across

    def _stats(v, w):
        ok = used & ~np.isnan(v)
        if not ok.any():
            return None
        return {"mean": float(v[ok].mean()), "median": float(np.median(v[ok])),
                "area_weighted_mean": float((v[ok] * w[ok]).sum() / w[ok].sum())}
    summary = {
        "method": ("ECD = 2 sqrt(A / pi) from MTEX grain area; caliper = extent of pixel centres projected on "
                   "the in-plane build direction (along) and its normal (across) + one step; aspect = along / "
                   "across; grains touching the map edge excluded (ISO 13067 / ASTM E2627)"),
        "min_px": min_px, "min_ecd_um": min_ecd_um, "step_um": step_um,
        "build_direction": [float(c) for c in bd], "bd_out_of_plane_deg": out_deg,
        "n_grains": int(n), "n_used": int(used.sum()), "n_edge": int(np.asarray(grain["is_edge"], bool).sum()),
        "ecd_um": _stats(ecd, area), "along_bd_um": _stats(along, area), "across_bd_um": _stats(across, area),
        "aspect_along_across": _stats(aspect, area), "warnings": warnings}
    table = {"id": ids, "ecd_um": ecd, "area_um2": area, "along_bd_um": along, "across_bd_um": across,
             "aspect": aspect, "used": used}
    return {"summary": summary, "table": table}
