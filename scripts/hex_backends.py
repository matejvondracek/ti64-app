"""Side-by-side picture of hexagonal cells drawn by both backends (decisions D20) for a visual check.

Exports a 12 x 9 um region of the JU02 300x300 crop (hexagonal grid, 0.2 um step) as an IPF map with
  - native (1 px per measurement, brick layout), 8x pixel-exact (regular hexagons, rasterised by the backend) and
  - a figure 120 mm wide at 600 DPI (>= 4 output px per measurement: MATLAB render.m uses hexRasterize, pymtex its
    own nearest-measurement raster)
with pymtex and with MATLAB (if available) and puts them side by side, enlarged without smoothing, into
projects/_gui_screenshots/hex_backends.png (outputs stay in projects/_hex_backends/, git-ignored).

Usage:  python scripts/hex_backends.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from ti64app import service  # noqa: E402
from ti64app.backends import get_backend  # noqa: E402
from ti64app.modules.jeol_strip import _font  # noqa: E402
from ti64app.project import Project  # noqa: E402

ROI = (10.0, 10.0, 22.0, 19.0)


def main() -> int:
    crop = next(iter(sorted((REPO / "data" / "test" / "crops").glob("*JU02*_crop300.ang"))), None)
    if crop is None:
        print("data/test/crops/*JU02*_crop300.ang missing")
        return 1
    work = REPO / "projects" / "_hex_backends"
    if work.exists():
        shutil.rmtree(work)
    p = Project.create("_hex_backends", base=REPO / "projects")
    service.run(p, "sample.set", {"map_id": "JU02", "specimen_id": "JU02", "map_file": str(crop),
                                  "input_state": "oim_cleaned", "build_direction": {"angle_deg": 270.0}})
    backends = [b for b in ("pymtex", "matlab") if get_backend(b).is_available()]
    cols = []
    for be in backends:
        service.run(p, "ebsd.load", {"map_id": "JU02"}, backend=be)
        out = {}
        for name, prm in (("native (brick)", {"mode": "native"}), ("8x (hexagons)", {"mode": "scale", "scale": 8}),
                          ("figure 120 mm / 600 DPI", {"mode": "width", "width_mm": 120.0, "dpi": 600,
                                                       "scale_bar": False, "colorbar": False, "legend": False})):
            s = service.run(p, "export.map", {"map_id": "JU02", "layer": {"kind": "ipf"}, "roi": list(ROI),
                                              "name": f"{be}_{name.split()[0]}", **prm}, backend=be)
            side = json.loads((p.root / s.outputs["sidecar"].path).read_text(encoding="utf-8"))
            out[name] = (Image.open(p.root / s.outputs["export"].path).convert("RGB"), side)
        cols.append((be, out))
        print(be, {k: v[1].get("cells", v[1].get("notes")) for k, v in out.items()})
    # layout: one column per backend, rows = native / 8x / figure, each shown 480 px wide (nearest neighbour)
    W, gap, head = 560, 24, 46
    rows = list(cols[0][1])
    hs = []
    for r in rows:
        im = cols[0][1][r][0]
        hs.append(int(round(W * im.height / im.width)))
    canvas = Image.new("RGB", (gap + len(cols) * (W + gap), head + sum(h + 40 for h in hs) + gap), "white")
    d = ImageDraw.Draw(canvas)
    f, fs = _font(16), _font(12)
    for c, (be, out) in enumerate(cols):
        x = gap + c * (W + gap)
        d.text((x, 12), f"{be}", fill=(0, 0, 0), font=f)
        y = head
        for r, h in zip(rows, hs):
            im, side = out[r]
            d.text((x, y), f"{r}: {im.width} × {im.height} px", fill=(60, 60, 60), font=fs)
            canvas.paste(im.resize((W, h), Image.Resampling.NEAREST), (x, y + 18))
            y += h + 40
    dst = REPO / "projects" / "_gui_screenshots" / "hex_backends.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)
    # zoomed detail of the 8x exports (about 6 x 4 cells), to see the cell shapes
    det = Image.new("RGB", (gap + len(cols) * (W + gap), W * 2 // 3 + 60), "white")
    dd = ImageDraw.Draw(det)
    for c, (be, out) in enumerate(cols):
        im = out["8x (hexagons)"][0]
        box = (int(im.width * 0.4), int(im.height * 0.4), int(im.width * 0.4) + 48, int(im.height * 0.4) + 32)
        x = gap + c * (W + gap)
        dd.text((x, 12), f"{be}: 8x export, detail (48 × 32 px enlarged)", fill=(0, 0, 0), font=f)
        det.paste(im.crop(box).resize((W, W * 2 // 3), Image.Resampling.NEAREST), (x, 44))
    det.save(dst.with_name("hex_backends_detail.png"))
    print(dst, dst.with_name("hex_backends_detail.png"))
    if len(cols) == 2:
        a = np.asarray(cols[0][1]["8x (hexagons)"][0], float)
        b = np.asarray(cols[1][1]["8x (hexagons)"][0], float)
        if a.shape == b.shape:
            print("8x exports: mean |pymtex - matlab| per channel", np.abs(a - b).mean())
    return 0


if __name__ == "__main__":
    sys.exit(main())
