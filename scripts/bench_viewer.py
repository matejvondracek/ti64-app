"""Viewer and export timings on a project (phase 5; numbers go to docs/performance.md).

Usage:  python scripts/bench_viewer.py [--project phase5_full] [--map JU02_full]
Times: layer colouring (IPF, KAM), base tiles at overview / 1:1 / zoomed-in levels, HAGB + LAGB overlay tiles,
pixel-exact export (native, 2x) and a figure export (120 mm at 600 DPI); reports process peak RAM.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

import numpy as np  # noqa: E402
import psutil  # noqa: E402

from ti64app import layers as lay  # noqa: E402
from ti64app import service  # noqa: E402
from ti64app.project import Project, projects_dir  # noqa: E402
from ti64app.raster import TILE, png_bytes, tile_colours, tile_grid  # noqa: E402


def peak() -> float:
    mi = psutil.Process().memory_info()
    return round(getattr(mi, "peak_wset", mi.rss) / 2**30, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="phase5_full")
    ap.add_argument("--map", default="JU02_full")
    a = ap.parse_args()
    p = Project.open(projects_dir() / a.project)
    rows = []

    def t(label, fn):
        t0 = time.perf_counter()
        r = fn()
        rows.append((label, round(time.perf_counter() - t0, 3), peak()))
        print(f"{label:55s} {rows[-1][1]:8.3f} s   peak {rows[-1][2]} GB", flush=True)
        return r
    L = t("layer IPF (backend colours + layout), first time", lambda: lay.get_layer(p, a.map, lay.LayerParams(kind="ipf")))
    lay._LAYERS.clear()
    t("layer IPF from the disk cache", lambda: lay.get_layer(p, a.map, lay.LayerParams(kind="ipf")))
    t("layer KAM", lambda: lay.get_layer(p, a.map, lay.LayerParams(kind="kam")))
    lo = L.layout
    x0, x1, y0, y1 = lo.extent
    for z, lab in ((-4, "overview (16 cells / px)"), (-2, "zoom -2 (4 cells / px)"), (0, "1:1 (cell = px)"), (3, "zoomed in 8x")):
        p_um = lo.cw / 2 ** z
        tx, ty = int((x0 + x1) / 2 / (TILE * p_um)), int((y0 + y1) / 2 / (TILE * p_um))
        t(f"base tile z={z} {lab}", lambda: png_bytes(tile_colours(lo, L.colors, tile_grid(lo.cw, z, tx, ty))))
    for name in ("hagb", "lagb"):
        O = t(f"overlay {name}: segment index", lambda: lay.get_overlay(p, a.map, name, lay.LayerParams()))
        print(f"   {O.index.n} segments")
        for z in (-4, 0):
            p_um = lo.cw / 2 ** z
            tx, ty = int((x0 + x1) / 2 / (TILE * p_um)), int((y0 + y1) / 2 / (TILE * p_um))

            def ov_tile():
                img = np.zeros((TILE, TILE, 4), np.uint8)
                O.index.draw(img, tile_grid(lo.cw, z, tx, ty), 1.0)
                return png_bytes(img)
            t(f"overlay tile {name} z={z}", ov_tile)
    t("export native PNG + HAGB", lambda: service.run(p, "export.map", {"map_id": a.map, "mode": "native", "overlays": ["hagb"], "annotations": False}))
    t("export 2x TIFF + HAGB", lambda: service.run(p, "export.map", {"map_id": a.map, "mode": "scale", "scale": 2, "format": "tiff", "overlays": ["hagb"], "annotations": False}))
    t("export figure 120 mm @ 600 DPI PNG + HAGB", lambda: service.run(p, "export.map", {"map_id": a.map, "mode": "width", "width_mm": 120, "dpi": 600, "overlays": ["hagb"], "annotations": False}))
    t("export figure 120 mm PDF + HAGB", lambda: service.run(p, "export.map", {"map_id": a.map, "mode": "width", "width_mm": 120, "dpi": 600, "format": "pdf", "overlays": ["hagb"], "annotations": False}))
    print("\n| step | s | peak RAM GB |\n| --- | --- | --- |")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]} |")


if __name__ == "__main__":
    main()
