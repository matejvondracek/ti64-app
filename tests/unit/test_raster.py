"""Pixel geometry of maps (ti64app.raster): synthetic hex and square grids, no data files needed."""
import numpy as np
import pytest

from ti64app.raster import (SegmentIndex, cell_layout, colour_image, exact_grid, index_image, rasterize_segments,
                            tile_colours, tile_grid, width_grid, write_raster)

DX = 0.2
DY = DX * np.sqrt(3) / 2


def hex_map(n_rows=6, n_cols=5, odd_shorter=True):
    """OIM hex grid in file order: even 0-based rows at x = k dx, odd rows shifted by dx/2 (one point fewer)."""
    xs, ys = [], []
    for r in range(n_rows):
        n = n_cols - (1 if (r % 2 and odd_shorter) else 0)
        x = np.arange(n) * DX + (DX / 2 if r % 2 else 0.0)
        xs.append(x)
        ys.append(np.full(n, r * DY))
    return np.concatenate(xs), np.concatenate(ys)


def square_map(n_rows=4, n_cols=6, step=0.5):
    y, x = np.mgrid[0:n_rows, 0:n_cols] * step
    return x.ravel(), y.ravel()


def test_every_measurement_samples_itself_hex_and_square():
    for (x, y), grid in ((hex_map(), "hex"), (square_map(), "square")):
        L = cell_layout(x, y, grid)
        assert np.array_equal(L.sample(x, y), np.arange(x.size))
        # a point a little inside the cell edge still belongs to it
        assert np.array_equal(L.sample(x + 0.45 * L.dx, y - 0.45 * L.dy), np.arange(x.size))
    L = cell_layout(*hex_map(), "hex")
    assert L.hex and L.cw == pytest.approx(DX / 2) and L.dy == pytest.approx(DY)
    assert L.sample(np.array([-1.0, 100.0]), np.array([0.0, 0.0])).tolist() == [-1, -1]


def test_native_hex_one_pixel_per_measurement_with_left_shift():
    x, y = hex_map(6, 5)
    L = cell_layout(x, y, "hex")
    g = exact_grid(L, 1)
    assert (g.W, g.H) == (5, 6)
    assert g.px == pytest.approx(DX) and g.py == pytest.approx(DY)
    assert any("left" in n for n in g.notes) and any("not square" in n for n in g.notes)
    idx = index_image(L, g)
    # even rows: pixel j = measurement j of the row; odd rows: pixel j = measurement j (drawn dx/2 left)
    starts = np.concatenate([[0], np.cumsum([5 - (r % 2) for r in range(6)])[:-1]])
    for r in range(6):
        n = 5 - (r % 2)
        assert idx[r, :n].tolist() == list(range(starts[r], starts[r] + n))
    assert idx[1, 4] == -1                       # the shorter odd row ends one pixel early


def test_scale_2_is_exact_on_hex_and_counts_pixels():
    x, y = hex_map(4, 5, odd_shorter=False)
    L = cell_layout(x, y, "hex")
    g = exact_grid(L, 2)
    assert (g.W, g.H) == (11, 8)                 # 5 points + half a point of shift, x2
    idx = index_image(L, g)
    counts = np.bincount(idx[idx >= 0], minlength=x.size)
    assert (counts == 4).all()                   # every measurement is exactly 2 x 2 pixels
    assert not g.notes[1:] or "left" not in " ".join(g.notes)


def test_square_scale_and_roi_snaps_to_whole_measurements():
    x, y = square_map(4, 6, 0.5)
    L = cell_layout(x, y, "square")
    g = exact_grid(L, 4)
    assert (g.W, g.H) == (24, 16) and g.px == pytest.approx(0.125)
    g = exact_grid(L, 1, roi=(0.6, 0.4, 1.4, 1.1))       # covers columns 1..3, rows 1..2 partially
    assert (g.W, g.H) == (2, 2) or (g.W, g.H) == (3, 2)
    assert g.x0 == pytest.approx(0.25) and g.y0 == pytest.approx(0.25)
    with pytest.raises(ValueError):
        exact_grid(L, 1, roi=(10, 10, 11, 11))
    w = width_grid(L, 30)
    assert w.W == 30 and w.px == pytest.approx(w.py) and w.H == 20


def test_colour_image_background_and_alpha():
    x, y = square_map(2, 2, 1.0)
    L = cell_layout(x, y, "square")
    cols = np.array([[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 0], [9, 9, 9, 255]], np.uint8)
    img = colour_image(L, exact_grid(L, 1), cols, background=(1, 2, 3, 4))
    assert img[0, 0].tolist() == [255, 0, 0, 255] and img[1, 0].tolist() == [1, 2, 3, 4]   # alpha 0 -> background


def test_rasterize_segments_brush_and_draw_order():
    img = np.zeros((10, 10, 4), np.uint8)
    rasterize_segments(img, [1.5], [5.5], [8.5], [5.5], [(1, 0, 0)], 1)
    assert img[5, 1:9, 0].tolist() == [255] * 8 and img[4].sum() == 0
    rasterize_segments(img, [1.5, 4.5], [5.5, 2.5], [8.5, 4.5], [5.5, 8.5], [(1, 0, 0), (0, 0, 1)], 3)
    assert img[5, 4, 2] == 255 and img[5, 4, 0] == 0          # later segment on top
    assert img[4, 2, 0] == 255                                # 3-pixel brush


def test_tile_colours_average_when_zoomed_out():
    x, y = square_map(8, 8, 1.0)
    L = cell_layout(x, y, "square")
    cols = np.zeros((64, 4), np.uint8)
    cols[:, 3] = 255
    cols[::2, 0] = 255                                        # alternating red / black columns
    g = tile_grid(L.cw, -2, 0, 0, tile=4)                     # 4 cells per pixel
    t = tile_colours(L, cols, g)
    assert 100 < t[0, 0, 0] < 160 and t[0, 0, 3] > 200        # averaged, not aliased
    t1 = tile_colours(L, cols, tile_grid(L.cw, 0, 0, 0, tile=4))
    assert set(t1[..., 0].ravel().tolist()) <= {0, 255}


def test_segment_index_finds_all_crossing_segments():
    rng = np.random.default_rng(1)
    n = 3000
    x1, y1 = rng.uniform(0, 100, n), rng.uniform(0, 100, n)
    x2, y2 = x1 + rng.uniform(-2, 2, n), y1 + rng.uniform(-2, 2, n)
    S = SegmentIndex({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, np.zeros((n, 3)), bin_um=7.0)
    box = (30.0, 40.0, 55.0, 61.0)
    q = set(S.query(*box).tolist())
    brute = np.flatnonzero((np.maximum(x1, x2) >= box[0]) & (np.minimum(x1, x2) <= box[2])
                           & (np.maximum(y1, y2) >= box[1]) & (np.minimum(y1, y2) <= box[3]))
    assert set(brute.tolist()) <= q
    assert np.all(np.diff(S.query(*box)) > 0)                 # original draw order


def test_write_raster_resolution_tags(tmp_path):
    from PIL import Image
    import tifffile

    img = np.zeros((5, 7, 4), np.uint8)
    f = write_raster(tmp_path / "a.png", img, (0.2, 0.1732), "desc")
    with Image.open(f) as im:
        assert im.size == (7, 5) and im.mode == "RGB"
        dx, dy = im.info["dpi"]
        assert dx == pytest.approx(25400 / 0.2, rel=1e-3) and dy == pytest.approx(25400 / 0.1732, rel=1e-3)
    f = write_raster(tmp_path / "a.tif", img, (0.2, 0.1732), "desc", transparent=True)
    with tifffile.TiffFile(f) as t:
        p = t.pages[0]
        assert p.shape == (5, 7, 4)
        xr = p.tags["XResolution"].value
        assert xr[0] / xr[1] == pytest.approx(1e4 / 0.2, rel=1e-4)
    with pytest.raises(ValueError):
        write_raster(tmp_path / "a.jpg", img, (1, 1))
