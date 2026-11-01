"""Build a demo project for the GUI (phase 5 visual checks and the Playwright click-through).

Usage:  python scripts/make_demo_project.py [--name phase5_demo] [--map crop1000|crop300|full] [--backend pymtex]
        [--no-recon] [--base projects]
Steps: sample sheet (JU02, OIM-cleaned, BD 270 deg), load, grains (HAGB 15 deg), KAM, prior-beta reconstruction
(unless --no-recon), a few annotations (arrow, text, crack path, ROI). Data come from data/test (git-ignored);
the project goes to projects/<name> (git-ignored). An existing project of that name is replaced.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from ti64app import service  # noqa: E402
from ti64app.project import Project, projects_dir  # noqa: E402

MAPS = {"crop300": ("crops", "*JU02*_crop300.ang"), "crop1000": ("crops", "*JU02*_crop1000.ang"),
        "full": ("", "*JU02*.ang")}


def build(name: str, which: str, backend: str, recon: bool, base: Path) -> Project:
    sub, pat = MAPS[which]
    f = next((REPO / "data" / "test" / sub).glob(pat))
    root = base / name
    if root.exists():
        shutil.rmtree(root)
    p = Project.create(name, base=base, backend=backend)
    mid = f"JU02_{which}"
    t = time.perf_counter()

    def run(op, params):
        s = service.run(p, op, params)
        print(f"{op:16s} {s.id} {time.perf_counter() - t:7.1f} s  {s.metrics}")
        return s
    run("sample.set", {"map_id": mid, "specimen_id": "JU02", "map_file": str(f), "input_state": "oim_cleaned",
                       "build_direction": {"angle_deg": 270.0}})
    run("ebsd.load", {"map_id": mid})
    run("grains.calc", {"map_id": mid})
    run("map.compute", {"map_id": mid, "kind": "kam"})
    if recon:
        run("recon.run", {"map_id": mid})
    from ti64app.raster import cell_layout  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    s = service.latest_step(p, ("ebsd.load",), map_id=mid)
    with np.load(p.root / s.outputs["ebsd"].path) as z:
        x0, x1, y0, y1 = cell_layout(z["x"], z["y"], "hex").extent
    W, H = x1 - x0, y1 - y0
    pt = lambda fx, fy: (x0 + fx * W, y0 + fy * H)  # noqa: E731
    run("annotations.set", {"map_id": mid, "items": [
        {"map_id": mid, "type": "arrow", "xy": [pt(0.1, 0.1), pt(0.25, 0.25)], "text": "A"},
        {"map_id": mid, "type": "text", "xy": [pt(0.6, 0.08)], "text": "lath colony"},
        {"map_id": mid, "type": "crack", "xy": [pt(0.05, 0.7), pt(0.3, 0.62), pt(0.55, 0.75), pt(0.8, 0.7)], "text": "crack 1"},
        {"map_id": mid, "type": "roi_rect", "xy": [pt(0.55, 0.3), pt(0.85, 0.55)], "text": "R1"}]})
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="phase5_demo")
    ap.add_argument("--map", default="crop1000", choices=list(MAPS))
    ap.add_argument("--backend", default="pymtex")
    ap.add_argument("--no-recon", action="store_true")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()
    p = build(a.name, a.map, a.backend, not a.no_recon, Path(a.base) if a.base else projects_dir())
    print("project:", p.root)
