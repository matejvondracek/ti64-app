"""Pixel geometry of EBSD maps, shared by both backends (decisions D15).

Colours come from the backend (`measurement_colors`: IPF key, colour map, phase colours); this module
only decides WHICH measurement a pixel shows and where overlay lines fall, so a pixel-exact export or a
viewer tile has the same geometry whichever backend coloured it.

Cell model ("brick" layout, the same as matlab/+ti64/hexRaster.m from EBSD_part2.m):
  hex grid:    every measurement covers [x - dx/2, x + dx/2) x [y - dy/2, y + dy/2); odd rows are shifted
               by dx/2, so the map is stored as half-width cells: measurement at half-column h covers
               half-cells h and h + 1 (cell width dx/2).
  square grid: one cell of dx x dy per measurement.
A pixel shows the measurement whose cell contains the pixel centre (nearest neighbour, no blending),
or nothing (-1).

Pixel-exact exports at scale k (1 = native, one pixel per measurement; 2, 4, 8 nearest-neighbour):
  pixel = dx/k x dy/k um.  On a hex grid the pixels are not square (dy = dx * sqrt(3)/2) and at k = 1 the
  odd rows cannot be shifted by half a pixel: they are drawn dx/2 to the LEFT of their true position
  (exact for even k). Both facts go into the sidecar and the image resolution tags.

Viewer tiles: Leaflet CRS with lng = x (um), lat = y (um, down), zoom 0 = `base_px_um` per screen pixel.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

EPS = 1e-7          # pixel centres exactly on a cell border go to the right / lower cell, reproducibly


@dataclass
class CellLayout:
    """Cells of a map in the brick layout. lut[r, c] = file-order index of the measurement, -1 = none."""
    lut: np.ndarray                 # (R, C) int32
    x0: float                       # left edge of half-cell / cell column 0 (um)
    y0: float                       # top edge of row 0 (um)
    cw: float                       # cell column width (dx/2 hex, dx square)
    dy: float                       # row height (um)
    dx: float                       # measurement step along x (um)
    hex: bool

    @property
    def width_um(self) -> float:
        return self.lut.shape[1] * self.cw

    @property
    def height_um(self) -> float:
        return self.lut.shape[0] * self.dy

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """(xmin, xmax, ymin, ymax) of the cells in um."""
        return self.x0, self.x0 + self.width_um, self.y0, self.y0 + self.height_um

    def sample(self, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        """Measurement index at points (um), -1 outside the map or on an empty cell."""
        c = np.floor((np.asarray(px, float) - self.x0) / self.cw + EPS).astype(np.int64)
        r = np.floor((np.asarray(py, float) - self.y0) / self.dy + EPS).astype(np.int64)
        R, C = self.lut.shape
        c, r = np.broadcast_arrays(c, r)
        ok = (r >= 0) & (r < R) & (c >= 0) & (c < C)
        out = np.full(c.shape, -1, np.int32)
        out[ok] = self.lut[r[ok], c[ok]]
        return out


def cell_layout(x: np.ndarray, y: np.ndarray, grid: str, step: float | None = None) -> CellLayout:
    """Brick layout of a map from its measurement coordinates (file order)."""
    x = np.asarray(x, float).ravel()
    y = np.asarray(y, float).ravel()
    ys = np.unique(np.round(y, 4))
    # row pitch from the full span (the .ang stores y with 5 decimals, so single steps are rounded)
    dy = float((y.max() - y.min()) / (ys.size - 1)) if ys.size > 1 else float(step or 1.0)
    row = np.round((y - y.min()) / dy).astype(np.int64)
    xr = np.sort(x[row == 0])
    dx = float((xr[-1] - xr[0]) / (xr.size - 1)) if xr.size > 1 else float(step or 1.0)
    xmin = float(x.min())
    hexg = grid == "hex"
    if hexg:
        h = np.round((x - xmin) / (dx / 2)).astype(np.int64)
        C = int(h.max()) + 2
        lut = np.full((int(row.max()) + 1, C), -1, np.int32)
        idx = np.arange(x.size, dtype=np.int32)
        lut[row, h] = idx
        lut[row, h + 1] = idx
        return CellLayout(lut, xmin - dx / 2, float(y.min()) - dy / 2, dx / 2, dy, dx, True)
    c = np.round((x - xmin) / dx).astype(np.int64)
    lut = np.full((int(row.max()) + 1, int(c.max()) + 1), -1, np.int32)
    lut[row, c] = np.arange(x.size, dtype=np.int32)
    return CellLayout(lut, xmin - dx / 2, float(y.min()) - dy / 2, dx, dy, dx, False)


# ---- pixel grids ----------------------------------------------------------------------------------
@dataclass
class PixelGrid:
    """A raster in map coordinates: pixel (i, j) has its centre at (x0 + (j + 0.5) px, y0 + (i + 0.5) py)."""
    x0: float
    y0: float
    px: float                       # um per pixel along x
    py: float                       # um per pixel along y
    W: int
    H: int
    notes: list[str] = field(default_factory=list)

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        return self.x0 + (np.arange(self.W) + 0.5) * self.px, self.y0 + (np.arange(self.H) + 0.5) * self.py

    def to_px(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Map coordinates (um) -> continuous pixel coordinates (0 = left / top edge)."""
        return (np.asarray(x, float) - self.x0) / self.px, (np.asarray(y, float) - self.y0) / self.py

    def meta(self) -> dict:
        return {"origin_um": [self.x0, self.y0], "pixel_size_um": [self.px, self.py],
                "size_px": [self.W, self.H], "notes": list(self.notes)}


def _roi_box(L: CellLayout, roi: tuple[float, float, float, float] | None):
    x0, x1, y0, y1 = L.extent
    if roi is None:
        return x0, x1, y0, y1
    rx0, ry0, rx1, ry1 = roi
    rx0, rx1 = sorted((rx0, rx1))
    ry0, ry1 = sorted((ry0, ry1))
    bx0, bx1, by0, by1 = max(x0, rx0), min(x1, rx1), max(y0, ry0), min(y1, ry1)
    if bx1 <= bx0 or by1 <= by0:
        raise ValueError(f"ROI {roi} does not overlap the map {L.extent}")
    return bx0, bx1, by0, by1


def exact_grid(L: CellLayout, scale: int = 1, roi: tuple[float, float, float, float] | None = None) -> PixelGrid:
    """Pixel-exact grid: one measurement = scale x scale pixels (pixel dx/scale x dy/scale um).
    roi = (x0, y0, x1, y1) in um is snapped outwards to whole measurement rows / columns."""
    if scale < 1 or int(scale) != scale:
        raise ValueError("scale must be a positive integer")
    px, py = L.dx / scale, L.dy / scale
    # whole measurements: columns of width dx starting at the even-row cell edge, rows of dy
    gx0, gy0 = L.x0, L.y0
    bx0, bx1, by0, by1 = _roi_box(L, roi)
    c0 = math.floor((bx0 - gx0) / L.dx + EPS)
    r0 = math.floor((by0 - gy0) / L.dy + EPS)
    X0, Y0 = gx0 + c0 * L.dx, gy0 + r0 * L.dy
    if L.hex and scale % 2:
        # at odd scales the shifted rows are drawn dx/2 left, so a last half column holds nothing new
        W = max(1, math.floor((bx1 - X0) / L.dx + EPS)) * scale
    else:
        W = math.ceil((bx1 - X0) / px - EPS)
    H = math.ceil((by1 - Y0) / py - EPS)
    H = int(math.ceil(H / scale) * scale)                  # whole measurement rows
    g = PixelGrid(X0, Y0, px, py, int(W), H)
    if L.hex:
        g.notes.append(f"hex grid: pixels are {px:.4g} x {py:.4g} um (not square)")
        if scale % 2:
            g.notes.append("hex grid at odd scale: the shifted rows are drawn half a measurement step (dx/2) "
                           "left of their true position")
    return g


def width_grid(L: CellLayout, width_px: int, roi: tuple[float, float, float, float] | None = None) -> PixelGrid:
    """Square pixels, `width_px` pixels across the map (or ROI); nearest-neighbour resampling."""
    bx0, bx1, by0, by1 = _roi_box(L, roi)
    p = (bx1 - bx0) / width_px
    return PixelGrid(bx0, by0, p, p, int(width_px), max(1, int(round((by1 - by0) / p))))


def index_image(L: CellLayout, g: PixelGrid, rows: slice | None = None) -> np.ndarray:
    """(H, W) measurement index of every pixel (nearest neighbour), -1 = none."""
    xc, yc = g.centres()
    if rows is not None:
        yc = yc[rows]
    return L.sample(xc[None, :], yc[:, None])


def colour_image(L: CellLayout, g: PixelGrid, colors: np.ndarray, background=(255, 255, 255, 255),
                 chunk_rows: int = 2048) -> np.ndarray:
    """(H, W, 4) uint8 image of per-measurement RGBA colours (n, 4) uint8; alpha 0 colours (not indexed,
    no value) and empty pixels get `background`."""
    colors = np.asarray(colors, np.uint8)
    out = np.empty((g.H, g.W, 4), np.uint8)
    bg = np.asarray(background, np.uint8)
    for r0 in range(0, g.H, chunk_rows):
        sl = slice(r0, min(g.H, r0 + chunk_rows))
        idx = index_image(L, g, sl)
        c = np.where((idx >= 0)[..., None], colors[np.clip(idx, 0, None)], bg)
        c[c[..., 3] == 0] = bg
        out[sl] = c
    return out


# ---- lines -------------------------------------------------------------------------------------------
def rasterize_segments(img: np.ndarray, x1, y1, x2, y2, rgb, width_px: float = 1.0,
                       chunk: int = 200_000) -> np.ndarray:
    """Draw line segments (continuous pixel coordinates) into an (H, W, 4) uint8 image in place.
    Square brush of round(width_px) pixels (>= 1), samples every half pixel; later segments are drawn on
    top (draw order = list order). rgb: (m, 3) floats 0..1 or (3,). Returns img."""
    H, W = img.shape[:2]
    x1, y1, x2, y2 = (np.asarray(a, float).ravel() for a in (x1, y1, x2, y2))
    m = x1.size
    if m == 0:
        return img
    col = np.asarray(rgb, float)
    col = np.broadcast_to(col.reshape(-1, 3) if col.size != 3 else col.reshape(1, 3), (m, 3))
    col8 = np.round(255 * np.clip(col, 0, 1)).astype(np.uint8)
    w = max(1, int(round(width_px)))
    offs = np.arange(w) - (w - 1) // 2
    # cull segments outside the image (with brush margin)
    mx0, mx1 = np.minimum(x1, x2), np.maximum(x1, x2)
    my0, my1 = np.minimum(y1, y2), np.maximum(y1, y2)
    keep = np.flatnonzero((mx1 >= -w) & (mx0 <= W + w) & (my1 >= -w) & (my0 <= H + w))
    for s in range(0, keep.size, chunk):
        k = keep[s:s + chunk]
        ln = np.hypot(x2[k] - x1[k], y2[k] - y1[k])
        ns = np.ceil(2 * ln).astype(np.int64) + 1
        seg = np.repeat(np.arange(k.size), ns)
        start = np.repeat(np.cumsum(ns) - ns, ns)
        t = (np.arange(seg.size) - start) / np.maximum(np.repeat(ns, ns) - 1, 1)
        xs = x1[k][seg] + t * (x2[k][seg] - x1[k][seg])
        ys = y1[k][seg] + t * (y2[k][seg] - y1[k][seg])
        cx = np.floor(xs).astype(np.int64)
        cy = np.floor(ys).astype(np.int64)
        for oy in offs:
            for ox in offs:
                X, Y = cx + ox, cy + oy
                ok = (X >= 0) & (X < W) & (Y >= 0) & (Y < H)
                # numpy fancy assignment: for repeated pixels the LAST write wins = draw order
                img[Y[ok], X[ok], :3] = col8[seg[ok]]
                img[Y[ok], X[ok], 3] = 255
    return img


def rasterize_polylines(img: np.ndarray, g: PixelGrid, lines: list[dict]) -> np.ndarray:
    """Overlay lines given in map coordinates: [{x1, y1, x2, y2 (arrays, um), rgb, width_px}]."""
    for L in lines:
        a, b = g.to_px(L["x1"], L["y1"])
        c, d = g.to_px(L["x2"], L["y2"])
        rasterize_segments(img, a, b, c, d, L["rgb"], L.get("width_px", 1.0))
    return img


# ---- viewer tiles ----------------------------------------------------------------------------------
TILE = 256


def tile_grid(base_px_um: float, z: int, tx: int, ty: int, tile: int = TILE) -> PixelGrid:
    p = base_px_um / 2.0 ** z
    return PixelGrid(tx * tile * p, ty * tile * p, p, p, tile, tile)


def tile_colours(L: CellLayout, colors: np.ndarray, g: PixelGrid, max_ss: int = 4) -> np.ndarray:
    """(tile, tile, 4) RGBA tile; zoomed out (pixel larger than a cell) it averages up to max_ss^2 samples
    per pixel so the overview is not aliased. Empty pixels are transparent."""
    ss = int(min(max_ss, max(1, math.ceil(g.px / min(L.cw, L.dy) - 1e-9))))
    colors = np.asarray(colors, np.uint8)
    if ss == 1:
        idx = index_image(L, g)
        out = np.zeros((g.H, g.W, 4), np.uint8)
        ok = idx >= 0
        out[ok] = colors[idx[ok]]
        return out
    sub = PixelGrid(g.x0, g.y0, g.px / ss, g.py / ss, g.W * ss, g.H * ss)
    idx = index_image(L, sub)
    c = np.where((idx >= 0)[..., None], colors[np.clip(idx, 0, None)], 0).astype(np.float32)
    a = c[..., 3:4] / 255.0
    c = c.reshape(g.H, ss, g.W, ss, 4)
    a = a.reshape(g.H, ss, g.W, ss, 1)
    wsum = a.sum(axis=(1, 3))
    rgb = (c[..., :3] * a).sum(axis=(1, 3)) / np.maximum(wsum, 1e-9)
    alpha = 255.0 * wsum / (ss * ss)
    return np.concatenate([rgb, alpha], -1).round().clip(0, 255).astype(np.uint8)


class SegmentIndex:
    """Line segments (um) binned on a coarse grid, so a tile only touches nearby segments."""

    def __init__(self, seg: dict[str, np.ndarray], rgb: np.ndarray, bin_um: float = 10.0) -> None:
        self.x1, self.y1, self.x2, self.y2 = (np.asarray(seg[k], float) for k in ("x1", "y1", "x2", "y2"))
        self.rgb = np.asarray(rgb, float).reshape(-1, 3)
        self.bin = bin_um
        n = self.x1.size
        self.n = n
        if n == 0:
            return
        cx = np.floor((self.x1 + self.x2) / 2 / bin_um).astype(np.int64)
        cy = np.floor((self.y1 + self.y2) / 2 / bin_um).astype(np.int64)
        self.cx0, self.cy0 = int(cx.min()), int(cy.min())
        self.nx = int(cx.max()) - self.cx0 + 1
        key = (cy - self.cy0) * self.nx + (cx - self.cx0)
        self.order = np.argsort(key, kind="stable")
        ks = key[self.order]
        self.ny = int(cy.max()) - self.cy0 + 1
        self.starts = np.searchsorted(ks, np.arange(self.nx * self.ny + 1))
        self.maxlen = float(np.max(np.hypot(self.x2 - self.x1, self.y2 - self.y1)))

    def query(self, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        """Indices of segments that may cross the box, in their original (draw) order."""
        if self.n == 0:
            return np.zeros(0, np.int64)
        m = self.maxlen / 2 + 1e-9
        bx0 = max(0, math.floor((x0 - m) / self.bin) - self.cx0)
        bx1 = min(self.nx - 1, math.floor((x1 + m) / self.bin) - self.cx0)
        by0 = max(0, math.floor((y0 - m) / self.bin) - self.cy0)
        by1 = min(self.ny - 1, math.floor((y1 + m) / self.bin) - self.cy0)
        if bx1 < bx0 or by1 < by0:
            return np.zeros(0, np.int64)
        parts = [self.order[self.starts[r * self.nx + bx0]: self.starts[r * self.nx + bx1 + 1]]
                 for r in range(by0, by1 + 1)]
        return np.sort(np.concatenate(parts)) if parts else np.zeros(0, np.int64)

    def draw(self, img: np.ndarray, g: PixelGrid, width_px: float = 1.0) -> np.ndarray:
        k = self.query(g.x0, g.y0, g.x0 + g.W * g.px, g.y0 + g.H * g.py)
        if k.size:
            a, b = g.to_px(self.x1[k], self.y1[k])
            c, d = g.to_px(self.x2[k], self.y2[k])
            rasterize_segments(img, a, b, c, d, self.rgb[k], width_px)
        return img


# ---- writers -------------------------------------------------------------------------------------------
def png_bytes(img: np.ndarray) -> bytes:
    from PIL import Image  # noqa: PLC0415

    buf = io.BytesIO()
    Image.fromarray(img, "RGBA" if img.shape[2] == 4 else "RGB").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def write_raster(path: Path, img: np.ndarray, px_um: tuple[float, float], description: str = "",
                 transparent: bool = False) -> Path:
    """PNG or TIFF with the physical pixel size in the resolution tags (separate x / y: non-square hex
    pixels are declared, not hidden). RGB unless `transparent`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = img if transparent else img[..., :3]
    ppcm = (1e4 / px_um[0], 1e4 / px_um[1])           # pixels per cm
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile  # noqa: PLC0415

        tifffile.imwrite(path, np.ascontiguousarray(arr), photometric="rgb", compression="zlib",
                         resolution=ppcm, resolutionunit="CENTIMETER", description=description or None,
                         metadata=None, software="ti64app")
    elif path.suffix.lower() == ".png":
        from PIL import Image, PngImagePlugin  # noqa: PLC0415

        info = PngImagePlugin.PngInfo()
        if description:
            info.add_itxt("Description", description)
        Image.fromarray(np.ascontiguousarray(arr), "RGBA" if transparent else "RGB").save(
            path, "PNG", dpi=(ppcm[0] * 2.54, ppcm[1] * 2.54), pnginfo=info)
    else:
        raise ValueError(f"pixel-exact export writes PNG or TIFF, not {path.suffix}")
    return path
