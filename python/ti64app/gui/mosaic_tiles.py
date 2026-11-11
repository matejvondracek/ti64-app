"""Zoomable stitch result (fix 3.16): viewer tiles of a mosaic TIFF read lazily from its pyramid, plus the 'tile
borders' and 'seams' overlays.

The mosaic TIFFs of sem.stitch are tiled (256 px, zlib) with a pyramid of 2x reductions when larger than 8192 px
(stitching.write_tiff). A viewer tile is drawn from the pyramid level whose pixels are just finer than the screen
pixels; only the TIFF tiles under it are read (file handle kept open, decoded tiles in a small LRU), so a
22 000 x 10 000 px mosaic opens at once and RAM stays at a few tens of MB. Mosaics without a pyramid (<= 8192 px)
are read whole (<= 64 MB as uint8) into the in-memory ImageLayer of tiles.py.

Route: /api/ti64/mosaic/{token}/{z}/{x}/{y}.png (own LRU; tiles.py stays untouched). Overlays are ordinary
layers.Overlay objects (segments in um) served by the overlay route of tiles.py.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi import Response
from nicegui import app

from ..layers import Overlay
from ..raster import SegmentIndex, png_bytes, tile_grid
from . import tiles

_SOURCES: dict[str, "MosaicSource"] = {}
_CACHE: OrderedDict[tuple, bytes] = OrderedDict()
_LOCK = threading.Lock()
MAX_TILES = 1500


class MosaicSource:
    """A tiled (pyramid) TIFF as viewer tiles in um: pixel size ps_um, only the first `height_px` rows (the info strip
    below the image is left out)."""

    def __init__(self, key: str, path: str | Path, ps_um: float, height_px: int | None = None,
                 max_cached: int = 384) -> None:
        import tifffile  # noqa: PLC0415

        self.key, self.path, self.ps = key, str(path), float(ps_um)
        self.tf = tifffile.TiffFile(self.path)
        levels = self.tf.series[0].levels
        self.pages = [lv.pages[0] if getattr(lv, "pages", None) else lv.keyframe for lv in levels]
        self.shape = tuple(self.pages[0].shape[:2])
        self.h = int(min(height_px or self.shape[0], self.shape[0]))
        self.io = threading.Lock()
        self.blocks: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self.max_cached = max_cached
        self.stretch = None
        if self.pages[0].dtype != np.uint8:               # 16-bit mosaic: contrast from the coarsest level
            a = self.pages[-1].asarray()[: max(1, self.h * self.pages[-1].shape[0] // self.shape[0])]
            lo, hi = np.percentile(a, (0.1, 99.9))
            self.stretch = (float(lo), max(float(hi - lo), 1e-9))

    @property
    def extent(self) -> list[float]:
        return [0.0, self.shape[1] * self.ps, 0.0, self.h * self.ps]

    def close(self) -> None:
        try:
            self.tf.close()
        except Exception:  # noqa: BLE001
            pass

    def _block(self, k: int, ty: int, tx: int) -> np.ndarray:
        key = (k, ty, tx)
        b = self.blocks.get(key)
        if b is not None:
            self.blocks.move_to_end(key)
            return b
        pg = self.pages[k]
        tl, tw = pg.tilelength, pg.tilewidth
        ntx = -(-pg.shape[1] // tw)
        i = ty * ntx + tx
        with self.io:
            fh = self.tf.filehandle
            fh.seek(pg.dataoffsets[i])
            data = fh.read(pg.databytecounts[i])
        seg = pg.decode(data, i)[0]
        b = np.asarray(seg).reshape(tl, tw)
        self.blocks[key] = b
        while len(self.blocks) > self.max_cached:
            self.blocks.popitem(last=False)
        return b

    def tile(self, g) -> np.ndarray:
        k = 0
        while k + 1 < len(self.pages) and self.ps * 2 ** (k + 1) <= g.px:
            k += 1
        pg = self.pages[k]
        f = 2 ** k
        psk = self.ps * f
        Hk, Wk = pg.shape[:2]
        hk = min(Hk, self.h // f)
        xc = g.x0 + (np.arange(g.W) + 0.5) * g.px
        yc = g.y0 + (np.arange(g.H) + 0.5) * g.py
        c = np.floor(xc / psk).astype(np.int64)
        r = np.floor(yc / psk).astype(np.int64)
        okc = (c >= 0) & (c < Wk)
        okr = (r >= 0) & (r < hk)
        out = np.zeros((g.H, g.W, 4), np.uint8)
        if not (okc.any() and okr.any()):
            return out
        tl, tw = pg.tilelength, pg.tilewidth
        cv, rv = c[okc], r[okr]
        ty0, ty1 = int(rv.min()) // tl, int(rv.max()) // tl
        tx0, tx1 = int(cv.min()) // tw, int(cv.max()) // tw
        reg = np.zeros(((ty1 - ty0 + 1) * tl, (tx1 - tx0 + 1) * tw), pg.dtype)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                reg[(ty - ty0) * tl:(ty - ty0 + 1) * tl, (tx - tx0) * tw:(tx - tx0 + 1) * tw] = self._block(k, ty, tx)
        v = reg[(rv - ty0 * tl)[:, None], (cv - tx0 * tw)[None, :]]
        if self.stretch is not None:
            v = np.clip((v.astype(np.float32) - self.stretch[0]) / self.stretch[1] * 255, 0, 255)
        v = v.astype(np.uint8)
        sub = out[np.ix_(okr, okc)]
        sub[..., 0] = sub[..., 1] = sub[..., 2] = v
        sub[..., 3] = 255
        out[np.ix_(okr, okc)] = sub
        return out


def open_mosaic(key: str, path: str | Path, ps_um: float, height_px: int | None = None) -> tuple[str, list[float]]:
    """Register a mosaic for the viewer; returns (tile URL, extent in um). Pyramid TIFFs are read lazily, small
    mosaics whole (tiles.ImageLayer)."""
    import tifffile  # noqa: PLC0415

    with tifffile.TiffFile(str(path)) as tf:
        n_levels = len(tf.series[0].levels)
    if n_levels > 1:
        old = _SOURCES.pop(key, None)
        if old is not None:
            old.close()
        src = MosaicSource(key, path, ps_um, height_px)
        _SOURCES[key] = src
        while len(_SOURCES) > 6:                          # a few open files at most
            _SOURCES.pop(next(iter(_SOURCES))).close()
        return f"/api/ti64/mosaic/{key}/{{z}}/{{x}}/{{y}}.png", src.extent
    img = tifffile.imread(str(path))
    if img.ndim > 2:
        img = img[..., 0]
    if height_px:
        img = img[:height_px]
    lay = tiles.ImageLayer(key, img, ps_um)
    return tiles.image_url(tiles.register_image(lay)), lay.extent


@app.get("/api/ti64/mosaic/{token}/{z}/{x}/{y}.png")
def _mosaic_tile(token: str, z: float, x: int, y: int) -> Response:
    src = _SOURCES.get(token)
    if src is None:
        return Response(status_code=404)
    key = (token, z, x, y)
    with _LOCK:
        data = _CACHE.get(key)
        if data is not None:
            _CACHE.move_to_end(key)
    if data is None:
        data = png_bytes(src.tile(tile_grid(src.ps, z, x, y)))
        with _LOCK:
            _CACHE[key] = data
            while len(_CACHE) > MAX_TILES:
                _CACHE.popitem(last=False)
    return Response(data, media_type="image/png", headers=tiles.HEADERS)


# ---- overlays ------------------------------------------------------------------------------------------------
def _hex_rgb(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _register(key: str, seg: dict[str, np.ndarray], rgb: np.ndarray, ps: float, width_px: float) -> str:
    n = len(seg["x1"])
    O = Overlay(key, SegmentIndex(seg, rgb if n else np.zeros((0, 3)), bin_um=max(64 * ps, 1e-6)), 1.0, [])
    return tiles.overlay_url(tiles.register_overlay(O, SimpleNamespace(cw=ps)), width_px)


def border_overlay(key: str, side: dict, colour_of, width_px: float = 2.0) -> str:
    """Tile borders (rectangles at the final tile positions) coloured by match score; colour_of(tile) -> '#rrggbb'."""
    ps = side.get("pixel_size_um") or 1.0
    seg: dict[str, list] = {"x1": [], "y1": [], "x2": [], "y2": []}
    rgb = []
    for t in side["tiles"]:
        x0, y0 = t["x_px"] * ps, t["y_px"] * ps
        x1, y1 = x0 + t["width_px"] * ps, y0 + t["height_px"] * ps
        col = _hex_rgb(colour_of(t))
        for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
            seg["x1"].append(a), seg["y1"].append(b), seg["x2"].append(c), seg["y2"].append(d)
            rgb.append(col)
    return _register(f"{key}_borders", {k: np.asarray(v, float) for k, v in seg.items()}, np.asarray(rgb), ps,
                     width_px)


def seam_segments_px(side: dict, seams_npz: Path | None) -> tuple[dict[str, np.ndarray], str]:
    """Seam lines in mosaic px: from the stitch step's seams output, else (older steps) the midline seams computed
    from the tile positions at a coarse scale. Returns (segments, source text)."""
    if seams_npz is not None and Path(seams_npz).is_file():
        with np.load(seams_npz) as z:
            return {k: z[k].astype(float) for k in ("x1", "y1", "x2", "y2")}, "seams of the stitch"
    from ..modules import stitching  # noqa: PLC0415

    W, H = side["mosaic_px"]
    k = max(1, math.ceil(max(W, H) / 2048))
    Q = np.array([[t["y_px"] / k, t["x_px"] / k] for t in side["tiles"]], float)
    shapes = [(max(1, int(round(t["height_px"] / k))), max(1, int(round(t["width_px"] / k)))) for t in side["tiles"]]
    canvas = (max(1, -(-H // k)), max(1, -(-W // k)))
    owner = np.full(canvas, -1, np.int16)
    stitching.seam_owner(Q, shapes, canvas, owner)
    seg = stitching.seam_segments(owner)
    return {n: v.astype(float) * k for n, v in seg.items()}, "midline seams (computed for display)"


def seam_overlay(key: str, side: dict, seams_npz: Path | None, colour: str = "#00c8ff",
                 width_px: float = 1.5) -> tuple[str, str, int]:
    """(overlay URL, source text, number of seam segments)."""
    ps = side.get("pixel_size_um") or 1.0
    seg, why = seam_segments_px(side, seams_npz)
    seg_um = {k: v * ps for k, v in seg.items()}
    n = len(seg_um["x1"])
    rgb = np.tile(np.asarray(_hex_rgb(colour)), (n, 1))
    return _register(f"{key}_seams", seg_um, rgb, ps, width_px), why, n
