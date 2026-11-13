"""What the probe tool shows for one measurement (EBSD tab status line; phase 6.6 fix 5 item 1).

Like the OIM Analysis status bar: x, y (um), Euler angles exactly as in the .ang file (degrees, the numbers OIM shows),
IQ, CI, phase; plus the app-frame Euler angles (after the EDAX load setting, frame.py) when they differ, both
labelled, and the scan row / column (0-based; hex grid: rows alternate NCOLS_ODD / NCOLS_EVEN points).
"""
from __future__ import annotations

import json

import numpy as np

from . import frame
from .project import Project


def row_col(y: np.ndarray, x: np.ndarray, i: int) -> tuple[int, int]:
    """0-based scan row and column of measurement i (file order)."""
    ys = np.unique(np.round(y, 4))
    dy = float(np.median(np.diff(ys))) if ys.size > 1 else 1.0
    row = int(round((y[i] - ys[0]) / dy))
    same = np.flatnonzero(np.abs(y - y[i]) < dy / 4)
    return row, int(np.searchsorted(np.sort(x[same]), x[i]))


def probe(project: Project, map_id: str, step_id: str, i: int) -> dict:
    """Values of measurement i (file order) of the map produced by step_id (a load or cleanup step)."""
    s = project.recipe().step(step_id)
    with np.load(project.root / s.outputs["ebsd"].path, allow_pickle=False) as z:
        x, y = z["x"], z["y"]
        q = z["quat"][i]
        out = {"index": int(i), "x_um": float(x[i]), "y_um": float(y[i]), "phase": int(z["phase"][i])}
        for k in ("ci", "iq", "recon_fit", "parent_grain"):
            if f"prop_{k}" in z.files:
                out[k] = float(z[f"prop_{k}"][i])
    out["row"], out["col"] = row_col(y, x, i)
    setting = 2
    load = next((t for t in reversed(project.recipe().steps) if t.op == "ebsd.load" and not t.stale
                 and t.params.get("map_id") == map_id), None)
    if load is not None and "meta" in load.outputs:
        try:
            setting = int(json.loads((project.root / load.outputs["meta"].path).read_text(encoding="utf-8"))
                          .get("setting", 2))
        except (OSError, ValueError):
            pass
    out["setting"] = setting
    sheet = project.sample(map_id)
    if str(sheet.map_file).lower().endswith(".ang"):
        e = frame.file_euler(sheet.map_file, project.cache_dir)
        if e.shape[0] == x.size:
            out["euler_file_deg"] = [float(v) for v in np.degrees(e[i])]
    if np.all(np.isfinite(q)):
        out["euler_app_deg"] = [float(v) for v in np.degrees(frame.quat2euler(q))]
        out["quat"] = [float(v) for v in q]
    return out


def probe_text(d: dict) -> str:
    """One status line: 'row 86, col 305 | x 61.00, y 14.90 µm | file Euler (147.4, 34.1, 98.4)° | app frame (setting
    2) Euler (302.6, 145.9, 278.4)° | IQ 78013.4 | CI 0.436 | α'."""
    names = {0: "not indexed", 1: "α", 2: "β"}
    parts = [f"row {d['row']}, col {d['col']}", f"x {d['x_um']:.2f}, y {d['y_um']:.2f} µm"]
    if "euler_file_deg" in d:
        parts.append("file Euler (" + ", ".join(f"{v:.1f}" for v in d["euler_file_deg"]) + ")° as in OIM")
    if "euler_app_deg" in d:
        same = "euler_file_deg" in d and np.allclose(d["euler_app_deg"], d["euler_file_deg"], atol=0.05)
        if not same:
            parts.append(f"app frame (setting {d['setting']}) Euler ("
                         + ", ".join(f"{v:.1f}" for v in d["euler_app_deg"]) + ")°")
    if "iq" in d:
        parts.append(f"IQ {d['iq']:.1f}")
    if "ci" in d:
        parts.append(f"CI {d['ci']:.3f}")
    parts.append(names.get(d["phase"], str(d["phase"])))
    for k in ("recon_fit", "parent_grain"):
        if k in d:
            parts.append(f"{k} {d[k]:.3g}")
    return " | ".join(parts)
