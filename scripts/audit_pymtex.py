"""Load an .ang with pymtex (mtex 1.0.0, EDAX setting 2) and report what the loader sees.

Usage:  python scripts/audit_pymtex.py <file.ang> <out.npz>
Prints a JSON summary to stdout; writes per-pixel x, y, phaseId, quaternion (a,b,c,d), ci, iq
of the non-padding pixels to <out.npz> (put it in a scratch/cache folder, never in data/ or the repo).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import psutil
from mtex import loadEBSD


def _arr(v):
    return np.asarray(v() if callable(v) else v)


def main(fname: str, out: str) -> dict:
    proc = psutil.Process()
    t0 = time.time()
    ebsd = loadEBSD(fname, setting=2)
    t_load = time.time() - t0

    pad = _arr(ebsd.isPadding).ravel()
    phase = _arr(ebsd.phaseId).ravel()
    x = _arr(ebsd.x).ravel()
    y = _arr(ebsd.y).ravel()
    keep = ~pad
    quat = np.full((phase.size, 4), np.nan)
    phases = {}
    for pid, cs in enumerate(ebsd.CSList):
        name = getattr(cs, "mineral", None)
        if pid == 0 or not name:
            continue
        sub = ebsd[name]
        o = sub.orientations
        q = np.stack([_arr(o.a).ravel(), _arr(o.b).ravel(), _arr(o.c).ravel(), _arr(o.d).ravel()], 1)
        m = phase == pid
        if m.sum() != q.shape[0]:  # sub-map order must match the flattened mask order
            raise RuntimeError(f"phase {name}: {m.sum()} pixels vs {q.shape[0]} orientations")
        quat[m] = q
        phases[name] = {
            "phaseId": pid,
            "n": int(m.sum()),
            "symmetry": str(getattr(cs, "pointGroup", "")),
            "abc": [float(v) for v in _arr(cs.abc).ravel()],
            "alignment": str(getattr(cs, "alignment", "")),
        }
    props = {k: _arr(v).ravel() for k, v in ebsd.prop.items()}
    np.savez(out, x=x[keep], y=y[keep], phase=phase[keep], quat=quat[keep],
             ci=props["ci"][keep], iq=props["iq"][keep], fit=props["fit"][keep])

    ci = props["ci"][keep]
    n = int(keep.sum())
    idx = phase[keep] > 0
    summary = {
        "file": Path(fname).name,
        "backend": "pymtex", "mtex_version": __import__("mtex").__version__ if hasattr(__import__("mtex"), "__version__") else "1.0.0",
        "load_s": round(t_load, 1),
        "rss_GB_after_load": round(proc.memory_info().rss / 2**30, 2),
        "grid_shape": list(ebsd.shape), "grid_size_incl_padding": int(phase.size),
        "n_padding": int(pad.sum()), "n_points": n,
        "isGridded": bool(_arr(ebsd.isGridded)), "layout": str(_arr(ebsd.layout)).splitlines()[0],
        "how2plot": str(ebsd.how2plot), "dPos": float(ebsd.dPos),
        "hex": "hex" in str(ebsd).lower(),
        "x_range": [float(x[keep].min()), float(x[keep].max())],
        "y_range": [float(y[keep].min()), float(y[keep].max())],
        "dx_unique_step": float(np.median(np.diff(np.unique(np.round(x[keep], 4))))),
        "dy_unique_step": float(np.median(np.diff(np.unique(np.round(y[keep], 4))))),
        "phases": phases,
        "phase_counts": {str(int(k)): int(v) for k, v in zip(*np.unique(phase[keep], return_counts=True))},
        "pct_indexed": 100 * float(idx.mean()),
        "props": sorted(ebsd.prop.keys()),
        "ci_mean_indexed": float(ci[idx].mean()),
        "repr": str(ebsd),
    }
    return summary


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), indent=1, ensure_ascii=False))
