"""Tile server for the map viewer: base-layer and overlay tiles at any zoom, as PNG.

Routes (registered on NiceGUI's FastAPI app):
  /api/ti64/tiles/{token}/{z}/{x}/{y}.png           base layer: backend colours placed by ti64app.raster
  /api/ti64/overlay/{token}/{z}/{x}/{y}.png?w=1.5   overlay segments, transparent, w = line width (px)
Tokens are registered by the viewer page (layer / overlay cache keys). Tiles are computed in FastAPI's
thread pool and kept in an LRU cache (a 256 x 256 tile takes ~5-30 ms on the full maps).
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np
from fastapi import Response
from nicegui import app

from ..layers import Layer, Overlay
from ..raster import TILE, png_bytes, tile_colours, tile_grid

_TOKENS: dict[str, tuple[str, object]] = {}
_CACHE: OrderedDict[tuple, bytes] = OrderedDict()
_LOCK = threading.Lock()
MAX_TILES = 3000
EMPTY: bytes | None = None


def register_layer(layer: Layer) -> str:
    _TOKENS[layer.key] = ("layer", layer)
    return layer.key


def register_overlay(ov: Overlay, layout) -> str:
    _TOKENS[ov.key] = ("overlay", (ov, layout))
    return ov.key


def layer_url(token: str) -> str:
    return f"/api/ti64/tiles/{token}/{{z}}/{{x}}/{{y}}.png"


def overlay_url(token: str, width_px: float) -> str:
    return f"/api/ti64/overlay/{token}/{{z}}/{{x}}/{{y}}.png?w={width_px:g}"


def _empty() -> bytes:
    global EMPTY
    if EMPTY is None:
        EMPTY = png_bytes(np.zeros((TILE, TILE, 4), np.uint8))
    return EMPTY


def _cached(key: tuple, make) -> bytes:
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    data = make()
    with _LOCK:
        _CACHE[key] = data
        while len(_CACHE) > MAX_TILES:
            _CACHE.popitem(last=False)
    return data


def render_layer_tile(layer: Layer, z: float, x: int, y: int) -> bytes:
    L = layer.layout
    g = tile_grid(L.cw, z, x, y)
    x0, x1, y0, y1 = L.extent
    if g.x0 > x1 or g.y0 > y1 or g.x0 + TILE * g.px < x0 or g.y0 + TILE * g.py < y0:
        return _empty()
    return png_bytes(tile_colours(L, layer.colors, g))


def render_overlay_tile(ov: Overlay, layout, z: float, x: int, y: int, w: float) -> bytes:
    g = tile_grid(layout.cw, z, x, y)
    img = np.zeros((TILE, TILE, 4), np.uint8)
    ov.index.draw(img, g, w * ov.widths)
    return png_bytes(img) if img[..., 3].any() else _empty()


HEADERS = {"Cache-Control": "max-age=3600"}


# ---- greyscale images / mosaics (View tab) ------------------------------------------------------------------
class ImageLayer:
    """A greyscale image as viewer tiles in um (pixel size ps_um): a pyramid of 2x block means, each tile drawn from
    the level whose pixels are just finer than the screen pixels (no aliasing when zoomed out)."""

    def __init__(self, key: str, img: np.ndarray, ps_um: float) -> None:
        self.key, self.ps = key, float(ps_um)
        a = img.astype(np.float32)
        if img.dtype != np.uint8:
            lo, hi = np.percentile(img[:: max(1, img.shape[0] // 512), :: max(1, img.shape[1] // 512)], (0.1, 99.9))
            a = (a - lo) / max(float(hi - lo), 1e-9) * 255
        self.levels = [np.clip(a, 0, 255).astype(np.uint8)]
        while max(self.levels[-1].shape) > 256:
            x = self.levels[-1]
            h, w = (x.shape[0] // 2) * 2, (x.shape[1] // 2) * 2
            if h == 0 or w == 0:
                break
            self.levels.append(x[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).astype(np.uint8))
        self.shape = img.shape[:2]

    @property
    def extent(self) -> list[float]:
        return [0.0, self.shape[1] * self.ps, 0.0, self.shape[0] * self.ps]

    def tile(self, g) -> np.ndarray:
        k = 0
        while k + 1 < len(self.levels) and self.ps * 2 ** (k + 1) <= g.px:
            k += 1
        lv, ps = self.levels[k], self.ps * 2 ** k
        xc = g.x0 + (np.arange(g.W) + 0.5) * g.px
        yc = g.y0 + (np.arange(g.H) + 0.5) * g.py
        c = np.floor(xc / ps).astype(np.int64)
        r = np.floor(yc / ps).astype(np.int64)
        okc = (c >= 0) & (c < lv.shape[1])
        okr = (r >= 0) & (r < lv.shape[0])
        out = np.zeros((g.H, g.W, 4), np.uint8)
        if okc.any() and okr.any():
            v = lv[np.clip(r, 0, lv.shape[0] - 1)[:, None], np.clip(c, 0, lv.shape[1] - 1)[None, :]]
            m = okr[:, None] & okc[None, :]
            out[..., 0] = out[..., 1] = out[..., 2] = v
            out[..., 3] = np.where(m, 255, 0)
        return out


def register_image(layer: ImageLayer) -> str:
    _TOKENS[layer.key] = ("image", layer)
    return layer.key


def image_url(token: str) -> str:
    return f"/api/ti64/image/{token}/{{z}}/{{x}}/{{y}}.png"


@app.get("/api/ti64/image/{token}/{z}/{x}/{y}.png")
def _image_tile(token: str, z: float, x: int, y: int) -> Response:
    kind, obj = _TOKENS.get(token, (None, None))
    if kind != "image":
        return Response(status_code=404)
    data = _cached(("I", token, z, x, y), lambda: png_bytes(obj.tile(tile_grid(obj.ps, z, x, y))))
    return Response(data, media_type="image/png", headers=HEADERS)


# ---- thumbnails (Stitch tab, Arrange view) ------------------------------------------------------------------
_THUMBS: dict[str, str] = {}


def thumb_url(path: str, strip_px: int = 0, max_px: int = 512) -> str:
    """URL of a PNG thumbnail (strip-free image area, longest side <= max_px) of an image file."""
    import hashlib  # noqa: PLC0415

    tok = hashlib.sha1(f"{path}|{strip_px}|{max_px}".encode()).hexdigest()[:16]
    _TOKENS[tok] = ("thumb", (path, strip_px, max_px))
    return f"/api/ti64/thumb/{tok}.png"


@app.get("/api/ti64/thumb/{token}.png")
def _thumb(token: str) -> Response:
    kind, obj = _TOKENS.get(token, (None, None))
    if kind != "thumb":
        return Response(status_code=404)
    path, strip_px, max_px = obj

    def make() -> bytes:
        import math  # noqa: PLC0415

        from ..io.bse import read_image  # noqa: PLC0415

        a = read_image(path)
        if strip_px:
            a = a[: a.shape[0] - strip_px]
        f = max(1, math.ceil(max(a.shape) / max_px))
        h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
        small = a[:h, :w].astype(np.float32).reshape(h // f, f, w // f, f).mean(axis=(1, 3))
        if a.dtype != np.uint8:
            lo, hi = np.percentile(small, (0.5, 99.5))
            small = (small - lo) / max(float(hi - lo), 1e-9) * 255
        g = np.clip(small, 0, 255).astype(np.uint8)
        return png_bytes(np.dstack([g, g, g]))
    return Response(_cached(("T", token), make), media_type="image/png", headers=HEADERS)


@app.get("/api/ti64/tiles/{token}/{z}/{x}/{y}.png")
def _tile(token: str, z: float, x: int, y: int) -> Response:
    kind, obj = _TOKENS.get(token, (None, None))
    if kind != "layer":
        return Response(status_code=404)
    data = _cached(("L", token, z, x, y), lambda: render_layer_tile(obj, z, x, y))
    return Response(data, media_type="image/png", headers=HEADERS)


@app.get("/api/ti64/overlay/{token}/{z}/{x}/{y}.png")
def _overlay(token: str, z: float, x: int, y: int, w: float = 1.0) -> Response:
    kind, obj = _TOKENS.get(token, (None, None))
    if kind != "overlay":
        return Response(status_code=404)
    ov, layout = obj
    data = _cached(("O", token, z, x, y, w), lambda: render_overlay_tile(ov, layout, z, x, y, w))
    return Response(data, media_type="image/png", headers=HEADERS)
