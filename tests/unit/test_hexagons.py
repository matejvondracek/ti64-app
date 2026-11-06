"""True hexagonal cells (decisions D20) on the JU02 300x300 crop (hexagonal grid, 0.2 um step).

Processing is on the hex grid; drawing used the brick layout (each cell a rectangle, odd rows shifted by half a step).
Now the viewer (cell >= 8 screen px) and exports with >= 4 px per measurement draw REGULAR HEXAGONS, rasterised by
the backend that coloured the map (pymtex: raster.hexagon_image; MATLAB: +ti64/hexRasterize.m, tests marked matlab).

Checks on an 8x export with a unique colour per measurement:
  - a cell is a hexagon: its pixel rows get narrower at the top and bottom (a brick cell has constant width),
    height ~ 2/sqrt(3) x width;
  - no straight vertical cell edge runs through two rows: the left edge of a cell is not continued by the left edge
    of the cell above / below;
  - neighbouring rows are offset by half a cell (centroid dx = 4 px at 8 px per step).
"""
import json

import numpy as np
import pytest

from tests.conftest import sample_params
from ti64app import raster, service
from ti64app.backends import get_backend


def _xy(crop):
    """Measurement centres (um, file order) from the .ang text (columns 4 and 5)."""
    a = np.loadtxt(crop, comments="#", usecols=(3, 4))
    return a[:, 0], a[:, 1]


def _unique_colours(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.int64) + 1
    c = np.zeros((n, 4), np.uint8)
    c[:, 0], c[:, 1], c[:, 2], c[:, 3] = k % 256, (k // 256) % 256, (k // 65536) % 256, 255
    return c


def _ids(img: np.ndarray) -> np.ndarray:
    a = img.astype(np.int64)
    return a[..., 0] + 256 * a[..., 1] + 65536 * a[..., 2] - 1


def _cell_at(L: raster.CellLayout, g: raster.PixelGrid) -> tuple[int, int]:
    """(row, half-column) of the brick cell at the centre of the pixel grid."""
    x = g.x0 + g.W * g.px / 2
    y = g.y0 + g.H * g.py / 2
    return int((y - L.y0) // L.dy), int((x - L.x0) // L.cw)


def check_hexagons(img: np.ndarray, L: raster.CellLayout, g: raster.PixelGrid, k: int = 8) -> dict:
    ids = _ids(img)
    # a measurement in the middle of the exported region and its neighbour in the next row
    r0, c0 = _cell_at(L, g)
    i = int(L.lut[r0, c0])
    m = ids == i
    rows = np.nonzero(m.any(axis=1))[0]
    widths = np.array([m[r].sum() for r in rows])
    assert widths.max() == pytest.approx(k, abs=1)                       # width = one step
    assert widths[0] < widths.max() - 2 and widths[-1] < widths.max() - 2, widths   # pointed top and bottom
    assert len(rows) == pytest.approx(2 / np.sqrt(3) * k, abs=1.5)        # regular hexagon height
    # neighbouring rows offset by half a cell
    j = int(L.lut[r0 + 1, c0])
    cx_i = np.nonzero(m)[1].mean()
    cx_j = np.nonzero(ids == j)[1].mean()
    off = abs(cx_j - cx_i) % k
    assert min(off, k - off) == pytest.approx(k / 2, abs=0.6)
    # no straight vertical edge through two rows: the left edge column of cell i (at its widest rows) does not
    # continue as a cell edge in the rows of the neighbouring row's cells directly below
    ys, xs = np.nonzero(m)
    left = xs.min()
    mid_rows = rows[widths == widths.max()]
    below = np.arange(mid_rows.max() + 1, min(ids.shape[0], mid_rows.max() + 1 + k // 2))
    edge_below = [ids[r, left] != ids[r, left - 1] for r in below if left > 0]
    assert not all(edge_below), "vertical edge continues into the next row (brick layout)"
    return {"rows": len(rows), "widths": widths.tolist(), "offset": float(off)}


def check_brick(img: np.ndarray, L: raster.CellLayout, g: raster.PixelGrid) -> None:
    ids = _ids(img)
    i = int(L.lut[_cell_at(L, g)])
    m = ids == i
    rows = np.nonzero(m.any(axis=1))[0]
    assert len({int(m[r].sum()) for r in rows}) == 1                     # a rectangle


def test_raster_hexagons_and_brick(crop300_ju02):
    x, y = _xy(crop300_ju02)
    L = raster.cell_layout(x, y, "hex")
    col = _unique_colours(x.size)
    g = raster.hexagon_grid(L, 8, roi=(10, 10, 20, 20))
    img = raster.colour_image(L, g, col, hexagons=True)
    check_hexagons(img, L, g)
    gb = raster.exact_grid(L, 2, roi=(10, 10, 20, 20))
    check_brick(raster.colour_image(L, gb, col), L, gb)
    # viewer: hexagons from 8 screen px per cell on, brick below
    assert raster.uses_hexagons(L, L.dx / 8) and not raster.uses_hexagons(L, L.dx / 7)


@pytest.mark.parametrize("backend", ["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
def test_backend_cell_image(backend, crop300_ju02):
    be = get_backend(backend)
    if not be.is_available():
        pytest.skip(f"{backend} unavailable: {be.unavailable_reason()}")
    x, y = _xy(crop300_ju02)
    L = raster.cell_layout(x, y, "hex")
    g = raster.hexagon_grid(L, 8, roi=(10, 10, 20, 20))
    img = be.cell_image(x, y, _unique_colours(x.size), g, (255, 255, 255, 255))
    assert img.shape == (g.H, g.W, 4)
    check_hexagons(img, L, g)
    if backend == "matlab":                      # same rule as the Python rasteriser: (almost) identical pixels
        ref = raster.hexagon_image(x, y, _unique_colours(x.size), g)
        assert (np.any(ref != img, axis=-1)).mean() < 0.002


@pytest.mark.parametrize("backend", ["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
def test_export_8x_hexagons_native_brick(backend, project, crop300_ju02):
    be = get_backend(backend)
    if not be.is_available():
        pytest.skip(f"{backend} unavailable: {be.unavailable_reason()}")
    service.run(project, "sample.set", sample_params(crop300_ju02))
    service.run(project, "ebsd.load", {"map_id": "JU02_c300"}, backend=backend)
    roi = (10.0, 10.0, 20.0, 20.0)
    s8 = service.run(project, "export.map", {"map_id": "JU02_c300", "layer": {"kind": "phase"}, "mode": "scale",
                                             "scale": 8, "roi": roi}, backend=backend)
    side = json.loads((project.root / s8.outputs["sidecar"].path).read_text(encoding="utf-8"))
    assert side["cells"].startswith("regular hexagons")
    assert side["pixel_size_um"][0] == pytest.approx(side["pixel_size_um"][1])        # square pixels
    assert side["pixel_size_um"][0] == pytest.approx(0.2 / 8, rel=1e-3)
    s1 = service.run(project, "export.map", {"map_id": "JU02_c300", "layer": {"kind": "phase"}, "mode": "native",
                                             "roi": roi}, backend=backend)
    side1 = json.loads((project.root / s1.outputs["sidecar"].path).read_text(encoding="utf-8"))
    assert side1["cells"].startswith("brick") and side1["scale"] == 1
