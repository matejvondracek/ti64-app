"""Data audit for TSL/OIM .ang files (text pass, no MTEX).

Reads the header and streams the data rows in chunks (peak RAM ~ a few hundred MB per map) and
prints a JSON summary: grid, step, map size, columns actually present, phase counts, % indexed,
CI / IQ / Fit / SEM statistics per phase, PRIAS presence.

Usage:  python scripts/audit_ang.py <file.ang> [more.ang ...] > summary.json
Writes nothing to disk.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def read_header(path: Path) -> tuple[dict, list[str], int]:
    """Return (parsed header dict, raw header lines, number of header lines)."""
    lines: list[str] = []
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            lines.append(line.rstrip("\n"))
    hdr: dict = {"phases": []}
    phase: dict | None = None
    for raw in lines:
        s = raw[1:].strip()
        if not s:
            continue
        key, _, val = s.partition(" ") if ":" not in s.split()[0] else s.partition(":")
        key, val = key.strip().rstrip(":"), val.strip()
        if key == "Phase" and val.isdigit():
            phase = {"id": int(val)}
            hdr["phases"].append(phase)
        elif phase is not None and key in ("MaterialName", "Symmetry", "PointGroupID",
                                           "SpaceGroupHall", "LatticeConstants"):
            phase[key] = val
        elif key in ("GRID", "XSTEP", "YSTEP", "NCOLS_ODD", "NCOLS_EVEN", "NROWS", "VERSION",
                     "COLUMN_COUNT", "COLUMN_HEADERS", "COLUMN_UNITS", "CartesianAlignment",
                     "WorkingDistance", "SampleTiltAngle", "x-star", "y-star", "z-star",
                     "SCANID", "OPERATOR"):
            hdr[key] = val
            if key in ("NCOLS_ODD", "NCOLS_EVEN", "NROWS"):
                phase = None  # phase blocks end before the grid block
    return hdr, lines, len(lines)


def stats(a: np.ndarray) -> dict:
    if a.size == 0:
        return {"n": 0}
    q = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
    return {"n": int(a.size), "min": float(a.min()), "mean": float(a.mean()),
            "p01": q[0], "p05": q[1], "p25": q[2], "median": q[3], "p75": q[4],
            "p95": q[5], "p99": q[6], "max": float(a.max())}


def audit(path: Path) -> dict:
    hdr, _, nhdr = read_header(path)
    ncol_counts: dict[int, int] = {}
    # first pass: count columns per row (cheap) while collecting data
    cols = []
    with open(path, "r", encoding="latin-1") as fh:
        for _ in range(nhdr):
            fh.readline()
        while True:
            chunk = fh.readlines(64 * 2**20)
            if not chunk:
                break
            lens = np.fromiter((len(l.split()) for l in chunk), dtype=np.int16, count=len(chunk))
            for v, c in zip(*np.unique(lens, return_counts=True)):
                ncol_counts[int(v)] = ncol_counts.get(int(v), 0) + int(c)
            ncol = int(lens.max())
            arr = np.loadtxt(chunk, dtype=np.float64, ndmin=2)
            cols.append(arr[:, :ncol].astype(np.float32))
    d = np.concatenate(cols)
    del cols
    phi1, Phi, phi2, x, y, iq, ci, ph, sem, fit = (d[:, i] for i in range(10))
    n = d.shape[0]
    xs = np.unique(np.round(x, 4))
    ys = np.unique(np.round(y, 4))
    nonidx_euler = (phi1 > 12) & (Phi > 12) & (phi2 > 12)  # OIM writes 4*pi for not indexed
    out = {
        "file": path.name,
        "size_MB": round(path.stat().st_size / 2**20, 1),
        "header": hdr,
        "n_points": int(n),
        "expected_points_from_header": None,
        "columns_per_row": ncol_counts,
        "declared_columns": int(hdr.get("COLUMN_COUNT", 0)),
        "prias_present_in_data": max(ncol_counts) >= 13,
        "x_range_um": [float(x.min()), float(x.max())],
        "y_range_um": [float(y.min()), float(y.max())],
        "n_unique_x": int(xs.size), "n_unique_y": int(ys.size),
        "dx_um": float(np.median(np.diff(xs))), "dy_um": float(np.median(np.diff(ys))),
        "phase_counts": {str(int(k)): int(v) for k, v in zip(*np.unique(ph, return_counts=True))},
        "pct_phase_ge1": 100 * float(np.mean(ph >= 1)),
        "pct_ci_gt_0.1": 100 * float(np.mean(ci > 0.1)),
        "pct_ci_ge_0": 100 * float(np.mean(ci >= 0)),
        "pct_ci_eq_-1": 100 * float(np.mean(ci == -1)),
        "n_euler_4pi": int(nonidx_euler.sum()),
        "fit_unique_values": [float(v) for v in np.unique(fit)[:10]],
        "sem_unique_values": [float(v) for v in np.unique(sem)[:10]],
        "per_phase": {},
    }
    nr = int(hdr.get("NROWS", 0)); no = int(hdr.get("NCOLS_ODD", 0)); ne = int(hdr.get("NCOLS_EVEN", 0))
    if nr:
        out["expected_points_from_header"] = (nr + 1) // 2 * no + nr // 2 * ne
    for p in np.unique(ph):
        m = ph == p
        out["per_phase"][str(int(p))] = {
            "n": int(m.sum()), "pct": 100 * float(m.mean()),
            "CI": stats(ci[m]), "IQ": stats(iq[m]), "Fit": stats(fit[m]),
        }
    out["all"] = {"CI": stats(ci), "IQ": stats(iq), "Fit": stats(fit)}
    return out


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(type(o))


if __name__ == "__main__":
    res = [audit(Path(f)) for f in sys.argv[1:]]
    json.dump(res, sys.stdout, indent=1, default=_default)
