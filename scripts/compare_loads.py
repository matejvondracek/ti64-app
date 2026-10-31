"""Compare the per-pixel exports of audit_pymtex.py (.npz) and audit_mtex.m (.h5) with the raw .ang.

Usage:  python scripts/compare_loads.py <file.ang> <pymtex.npz> <mtex.h5>
Prints a JSON report: pixel matching, phase agreement, orientation differences (raw quaternion
and symmetry-reduced misorientation), CI/IQ differences, and the constant rotation that the
EDAX 'setting 2' load applied relative to the file's Bunge Euler angles.
"""
from __future__ import annotations

import json
import sys

import h5py
import numpy as np
from orix.quaternion import Orientation, Rotation
from orix.quaternion.symmetry import D6h, Oh

STEP_X, STEP_Y = 0.1, 0.17320508  # half the hex column spacing, hex row spacing


def key(x, y):
    """Integer pixel key from coordinates (hex grid: x in 0.1 um units, y in rows)."""
    return np.round(np.asarray(y) / STEP_Y).astype(np.int64) * 100000 + np.round(np.asarray(x) / STEP_X).astype(np.int64)


def read_ang(path):
    with open(path, "r", encoding="latin-1") as fh:
        nh = sum(1 for _ in iter(lambda: fh.readline().startswith("#"), False))
    d = np.loadtxt(path, skiprows=nh, usecols=range(10), dtype=np.float64, ndmin=2)
    return d


def misori_deg(qa, qb, sym):
    """Smallest rotation angle between orientations qa and qb (n x 4, a,b,c,d) under crystal symmetry."""
    oa = Orientation(qa, symmetry=sym)
    ob = Orientation(qb, symmetry=sym)
    return np.rad2deg(oa.angle_with(ob))


def main(ang, npz, h5):
    raw = read_ang(ang)
    rk = key(raw[:, 3], raw[:, 4])
    P = np.load(npz)
    with h5py.File(h5, "r") as f:
        M = {k: f[k][()] for k in f.keys()}
    M = {k: (v.T if v.ndim == 2 and v.shape[0] in (1, 4) and v.shape[1] != 4 else v) for k, v in M.items()}
    M = {k: (v.ravel() if v.ndim == 2 and min(v.shape) == 1 else v) for k, v in M.items()}
    pk, mk = key(P["x"], P["y"]), key(M["x"], M["y"])
    out = {"n_raw": int(rk.size), "n_pymtex": int(pk.size), "n_mtex": int(mk.size),
           "unique_keys": [int(np.unique(rk).size), int(np.unique(pk).size), int(np.unique(mk).size)]}
    # order everything like the file
    pi = np.argsort(pk); mi = np.argsort(mk); ri = np.argsort(rk)
    same = np.array_equal(pk[pi], rk[ri]) and np.array_equal(mk[mi], rk[ri])
    out["same_pixel_set"] = bool(same)
    inv_r = np.empty_like(ri); inv_r[ri] = np.arange(ri.size)
    p_ord = pi[inv_r]; m_ord = mi[inv_r]          # index into P / M for each file row
    out["mtex_oldId_equals_file_row"] = bool(np.array_equal(M["oldId"][m_ord].astype(np.int64), np.arange(1, rk.size + 1)))
    ph_raw = raw[:, 7].astype(int); ph_raw[ph_raw < 0] = 0
    ph_p = P["phase"][p_ord].astype(int); ph_m = M["phase"][m_ord].astype(int)
    out["phase_agree_pymtex_vs_file"] = float(np.mean(ph_p == ph_raw))
    out["phase_agree_mtex_vs_file"] = float(np.mean(ph_m == ph_raw))
    out["ci_maxabs_pymtex_minus_mtex"] = float(np.max(np.abs(P["ci"][p_ord] - M["ci"][m_ord])))
    out["iq_maxabs_pymtex_minus_mtex"] = float(np.max(np.abs(P["iq"][p_ord] - M["iq"][m_ord])))
    out["ci_maxabs_pymtex_minus_file"] = float(np.max(np.abs(P["ci"][p_ord] - raw[:, 6])))
    qp = P["quat"][p_ord]; qm = M["quat"][m_ord]
    q_file = Rotation.from_euler(raw[:, :3], direction="crystal2lab").data  # Bunge; MTEX convention (crystal -> specimen)
    for pid, sym, name in ((1, D6h, "alpha"), (2, Oh, "beta")):
        m = ph_raw == pid
        a, b, f = qp[m], qm[m], q_file[m]
        raw_ang = np.rad2deg(2 * np.arccos(np.clip(np.abs(np.sum(a * b, 1)), 0, 1)))
        mis = misori_deg(a, b, sym)
        # constant specimen-frame rotation r with  q_loaded = r * q_file  (EDAX setting 2)
        rel = Rotation(b) * ~Rotation(f)
        rel_mean = rel[:1]
        spread = np.rad2deg((rel * ~rel_mean).angle)
        out[name] = {
            "n": int(m.sum()),
            "raw_quat_angle_pymtex_vs_mtex_deg": {"max": float(raw_ang.max()), "mean": float(raw_ang.mean())},
            "misorientation_pymtex_vs_mtex_deg": {"max": float(mis.max()), "mean": float(mis.mean()),
                                                  "n_gt_0.01": int(np.sum(mis > 0.01))},
            "mtex_equals_r_times_file_euler": {"euler_deg": np.rad2deg(rel_mean.to_euler()).round(3).tolist(),
                                           "axis_angle": [np.round(rel_mean.axis.data, 4).tolist(), float(np.rad2deg(rel_mean.angle[0]))],
                                           "spread_max_deg": float(np.nanmax(spread))},
        }
    return out


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:4]), indent=1))
