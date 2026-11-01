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
