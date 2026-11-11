"""SEM image stitching (modules/stitching.py, jeol_strip.py) on SYNTHETIC tiles: a textured image cut into overlapping
tiles with known subpixel offsets, noise, per-tile brightness / contrast changes and a fake JEOL data bar, with and
without .txt sidecars. Real JEOL data: tests/integration/test_stitching_real.py."""
from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile
from PIL import Image
from scipy.ndimage import gaussian_filter, shift as ndshift

from ti64app import service
from ti64app.modules import jeol_strip, stitching

PS = 0.05            # um / px
TW, TH, BAR = 640, 480, 32          # tile image area 4:3 + data bar (file 640 x 512)


def _texture(h: int, w: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = gaussian_filter(rng.normal(size=(h, w)), 2.0) * 3.0
    a += gaussian_filter(rng.normal(size=(h, w)), 12.0) * 20.0
    for _ in range(300):                                 # bright particles, like alpha/beta contrast
        y, x = rng.integers(0, h), rng.integers(0, w)
        r = rng.integers(2, 8)
        a[max(0, y - r): y + r, max(0, x - r): x + r] += rng.uniform(1, 3)
    a = (a - a.min()) / (a.max() - a.min())
    return (40 + 170 * a).astype(np.float32)


def _data_bar(width: int, height: int, number: int) -> np.ndarray:
    bar = np.zeros((height, width), np.uint8)
    bar[height // 6: height // 2, 10: 10 + 6 * (number % 7 + 3)] = 255          # "text"
    bar[height // 6: height // 2 - 2, width - 220: width - 100] = 255            # scale bar
    bar[height // 2 + 4: height - 4, 12: 60] = 255
    return bar


TXT = """$CM_FORMAT JEOL/MP
$CM_DATE {date}
$CM_TIME 12:00:{sec:02d}
$CM_INSTRUMENT JSM-IT500HR
$CM_IMAGE_SIZE {tw} {th}
$CM_MAG {mag}
$CM_STAGE_POSITION {x:.3f} {y:.3f} 14.000 241.120 0.000 0.000
$CM_ACCEL_VOLT 20.0
$CM_FIELD_OF_VIEW {fw:.2f}µm {fh:.2f}µm
$SM_MICRON_MARKER 5µm
$SM_MICRON_BAR 100
$SM_PNU_HEIGHT {bar}
$SM_DETECTOR {det}
$SM_WD {wd}
$SM_SCAN_ROTATION 0
$SM_IMAGE_NUMBER {num}
$MP_PROBE_NUMBER 60.0
$MP_BEAM_SHIFT -0.05 0.01
"""


def make_tiles(tmp, positions, *, txt=True, stage_map=stitching.DEFAULT_STAGE_MAP, det="BED-C", seed=1,
               gains=None, noise=4.0, wd=None, mag=4000, name_det=None, base=None, start=9771, origin=None):
    """Cut tiles at subpixel `positions` (row, col px) from one texture; returns files and the true positions."""
    positions = np.asarray(positions, float)
    positions = positions - (positions.min(axis=0) if origin is None else np.asarray(origin, float)) + 4.0
    H = int(positions[:, 0].max()) + TH + 8
    W = int(positions[:, 1].max()) + TW + 8
    big = base if base is not None else _texture(H, W, seed)
    rng = np.random.default_rng(seed + 10)
    Minv = np.linalg.inv(np.asarray(stage_map, float))         # (col, row) px*ps -> (X, Y) um
    files = []
    for k, (r, c) in enumerate(positions):
        ir, ic = int(np.floor(r)), int(np.floor(c))
        sub = ndshift(big[ir: ir + TH + 2, ic: ic + TW + 2], (-(r - ir), -(c - ic)), order=3)[:TH, :TW]
        g, o = (gains[k] if gains else (1.0, 0.0))
        tile = sub * g + o + rng.normal(0, noise, sub.shape)
        img = np.vstack([np.clip(np.rint(tile), 0, 255).astype(np.uint8), _data_bar(TW, BAR, k)])
        num = start + k
        stem = f"{num}_SYN_{name_det or det}_2"
        path = tmp / f"{stem}.bmp"
        Image.fromarray(img).save(path)
        if txt:
            X, Y = Minv @ np.array([c * PS, r * PS])           # um; stage rounded to 1 um like the JSM-IT500HR
            (tmp / f"{stem}.txt").write_text(TXT.format(
                date="2026-10-29", sec=k, tw=TW, th=TH, mag=mag, x=round(X) / 1000 + 1.0, y=round(Y) / 1000 + 20.0,
                fw=TW * PS, fh=TH * PS, bar=BAR, det=det, wd=(wd[k] if wd else 11.3), num=num), encoding="utf-8")
        files.append(path)
    return files, positions


def _rel(side, positions, names):
    est = {t["name"]: (t["y_px"], t["x_px"]) for t in side["tiles"]}
    e = np.array([est[n] for n in names])
    return (e - e[0]) - (positions - positions[0])


GRID = [(0.0, 0.0), (3.4, 512.7), (380.25, -2.6), (383.8, 509.1), (761.5, 1.3), (764.75, 514.2)]   # 3 x 2, ~20 % overlap


def test_detector_classification():
    assert stitching.classify_detector("BED-C") == "BSE" and stitching.classify_detector("BED-T") == "BSE"
    assert stitching.classify_detector("SED") == "SE" and stitching.classify_detector("LED") == "SE"
    assert stitching.classify_detector("EDS") == "other"
    assert stitching.detector_from_filename("9771_MVH1_JU05_BED-C_2") == "BED-C"
    assert stitching.detector_from_filename("9771_MVH1_JU05_SED_1") == "SED"


def test_scan_counts_and_txt_over_filename(tmp_path):
    make_tiles(tmp_path, GRID[:2], det="BED-C")
    make_tiles(tmp_path, GRID[:2], det="SED", seed=1, start=9771)
    # a file name saying BED but a .txt saying SED: the .txt wins
    make_tiles(tmp_path, GRID[:1], det="SED", name_det="BED-T", start=9900)
    tiles = stitching.scan(tmp_path)
    assert stitching.detector_counts(tiles) == {"BSE": 2, "SE": 3}
    t = next(t for t in tiles if t.image_number == 9900)
    assert t.detector == "SED" and t.detector_source == "txt" and t.detector_class == "SE"
    assert {t.strip_px for t in tiles} == {BAR} and {t.strip_source for t in tiles} == {"txt"}
    assert tiles[0].pixel_size_um == pytest.approx(PS)


@pytest.mark.parametrize("stage_map", [stitching.DEFAULT_STAGE_MAP, ((1, 0), (0, -1)), ((0, 1), (1, 0))])
def test_recover_offsets_with_stage(tmp_path, stage_map):
    gains = [(1.0, 0.0), (1.15, -12.0), (0.9, 10.0), (1.05, 5.0), (0.85, 20.0), (1.1, -8.0)]
    files, pos = make_tiles(tmp_path, GRID, stage_map=stage_map, gains=gains)
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "out")
    side = res["BSE"].info
    assert side["stage_map"]["chosen"] == [list(r) for r in stage_map]          # mapping found from the data
    err = _rel(side, pos, [f.stem for f in files])
    assert np.abs(err).max() < 1.0, err
    assert side["stage_only_tiles"] == [] and side["residual_px"]["max"] < 1.0
    assert side["mosaic_px"][1] == pytest.approx(pos[:, 0].max() - pos[:, 0].min() + TH, abs=2)
    # analysis pixels are strip-free: no black data-bar rows inside the mosaic
    m = tifffile.imread(res["BSE"].tiff)
    assert m.shape == (side["mosaic_px"][1], side["mosaic_px"][0])
    assert (m == 0).mean() < 0.01
    tf = side["stage_to_mosaic"]
    assert tf["axis_map"] == [list(r) for r in stage_map] and tf["rms_px"] < 25     # stage rounding 1 um = 20 px


def test_recover_offsets_without_txt(tmp_path):
    files, pos = make_tiles(tmp_path, GRID, txt=False, gains=[(1.0, 0), (1.2, -10), (0.9, 5), (1, 0), (1.1, 3), (1, 0)])
    tiles = stitching.scan(tmp_path)
    assert {t.strip_px for t in tiles} == {BAR}
    assert {t.strip_source for t in tiles} == {"4:3 rule (detected)"}
    assert {t.detector for t in tiles} == {"BED-C"} and {t.detector_source for t in tiles} == {"file name"}
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "out", tiles=tiles)
    err = _rel(res["BSE"].info, pos, [f.stem for f in files])
    assert np.abs(err).max() < 1.0, err


def test_detect_strip():
    img = np.vstack([np.full((480, 640), 120, np.uint8) + np.random.default_rng(0).integers(0, 40, (480, 640),
                                                                                           dtype=np.uint8),
                     _data_bar(640, 32, 3)])
    assert stitching.detect_strip(img) == 32
    assert stitching.detect_strip(img, expected=32) == 32
    assert stitching.detect_strip(img[:480]) is None


def test_blending_normalisation_and_raw(tmp_path):
    gains = [(1.0, 0.0), (1.3, -30.0), (0.8, 25.0), (1.0, 0.0), (1.2, -10.0), (0.9, 15.0)]
    base = _texture(int(max(r for r, _ in GRID)) + TH + 16, int(max(c for _, c in GRID)) + TW + 16, 3)
    files, pos = make_tiles(tmp_path, [(round(r), round(c)) for r, c in GRID], gains=gains, noise=0.0, base=base)
    feather = stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "f")["BSE"]
    raw = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), blend="none"), tmp_path / "r")["BSE"]
    assert feather.info["normalize"] is True and raw.info["normalize"] is False
    mf = tifffile.imread(feather.tiff).astype(float)
    mr = tifffile.imread(raw.tiff)
    # normalised + feathered mosaic follows the ground truth (gains removed up to a global linear map)
    side = feather.info
    t0 = next(t for t in side["tiles"] if t["name"] == files[0].stem)
    r0, c0 = int(round(pos[0][0] - t0["y_px"])), int(round(pos[0][1] - t0["x_px"]))
    gt = base[r0: r0 + mf.shape[0], c0: c0 + mf.shape[1]]
    h, w = min(gt.shape[0], mf.shape[0]), min(gt.shape[1], mf.shape[1])
    ok = mf[:h, :w] > 0
    assert np.corrcoef(gt[:h, :w][ok], mf[:h, :w][ok])[0, 1] > 0.97
    # no blending: every mosaic pixel is a raw pixel of one tile (tile 0 at its integer position)
    tr = next(t for t in raw.info["tiles"] if t["name"] == files[0].stem)
    tile0 = np.asarray(Image.open(files[0]))[:TH]
    y, x = tr["y_int_px"], tr["x_int_px"]
    np.testing.assert_array_equal(mr[y + 10: y + 100, x + 10: x + 100], tile0[10:100, 10:100])


def test_exclude_and_nudge(tmp_path):
    files, pos = make_tiles(tmp_path, GRID)
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), exclude=["9776"],
                                                  nudge={"9771": (5.0, -3.0)}), tmp_path / "o")["BSE"]
    names = [t["name"] for t in res.info["tiles"]]
    assert files[5].stem not in names and len(names) == 5
    t0 = next(t for t in res.info["tiles"] if t["name"] == files[0].stem)
    assert (t0["nudge_dx_px"], t0["nudge_dy_px"]) == (5.0, -3.0)


def test_refuses_incompatible_tiles(tmp_path):
    make_tiles(tmp_path, GRID[:2])
    make_tiles(tmp_path, GRID[2:4], mag=8000, start=9790)
    with pytest.raises(ValueError, match="mixes magnifications.*2 stitch sets"):
        stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "o")


def test_stitch_sets_magnification_series(tmp_path):
    """A JU04-like folder: a magnification series (never stitched together) + two overlapping groups at one
    magnification that are far apart on the stage (two sets)."""
    make_tiles(tmp_path, GRID[:3])                                     # 4000x, overlapping
    make_tiles(tmp_path, GRID[:2], mag=8000, start=9790)               # another magnification, same place
    far = [(r + 40000.0, c) for r, c in GRID[:2]]                       # 2 mm away on the stage
    make_tiles(tmp_path, far, start=9800, origin=np.min(np.asarray(GRID), axis=0))
    make_tiles(tmp_path, [(0.0, 0.0)], mag=23, start=9700)             # an overview: single image
    sets = stitching.stitch_sets(stitching.scan(tmp_path))
    by = {tuple(st.image_numbers): st for st in sets}
    assert (9771, 9772, 9773) in by and (9790, 9791) in by and (9800, 9801) in by and (9700,) in by
    assert by[(9771, 9772, 9773)].overlapping and not by[(9700,)].overlapping
    assert by[(9700,)].note.startswith("single image")
    assert "4000×" in by[(9771, 9772, 9773)].label and "9771–9773" in by[(9771, 9772, 9773)].label
    files = by[(9790, 9791)].paths
    res = stitching.stitch(stitching.StitchParams(files=files), tmp_path / "o")["BSE"]
    assert res.info["n_tiles"] == 2


def _truth_on_canvas(base, side, names, pos):
    """Ground-truth texture sampled on the mosaic canvas (cubic), using tile 0's placement."""
    from scipy.ndimage import map_coordinates
    t0 = next(t for t in side["tiles"] if t["name"] == names[0])
    off = (pos[0][0] - t0["y_px"], pos[0][1] - t0["x_px"])          # canvas -> base coordinates
    W, H = side["mosaic_px"]
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    return map_coordinates(base, [yy + off[0], xx + off[1]], order=3, mode="nearest")


def test_subpixel_placement_no_ghosting(tmp_path):
    """Subpixel offsets: the mosaic matches the ground truth in the seam bands to within the pixel noise (no double
    edges), and the residual misalignment after placement is far below a pixel."""
    noise = 4.0
    base = _texture(int(max(r for r, _ in GRID)) + TH + 16, int(max(c for _, c in GRID)) + TW + 16, 11)
    files, pos = make_tiles(tmp_path, GRID, base=base, noise=noise)
    names = [f.stem for f in files]
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), normalize=False), tmp_path / "o")["BSE"]
    side = res.info
    assert side["render"]["blend"] == "seam" and side["render"]["blend_band_px"] == 32
    assert "subpixel" in side["placement"]
    used = [q for q in side["pairs"] if q["used"]]
    assert used and max(q["misalignment_px"] for q in used) < 0.15, [q["misalignment_px"] for q in used]
    assert side["misalignment_px"]["max"] < 0.15
    # in the overlaps, the two placed tiles differ only by noise (sqrt(2) * sigma; mean |N(0, s)| = 0.8 s)
    for q in used:
        assert q["diff_mean"] < 1.6 * noise, q
    m = tifffile.imread(res.tiff).astype(float)
    gt = _truth_on_canvas(base, side, names, pos)
    # seam bands = pixels covered by two tiles near the seam: compare there
    cover = np.zeros(m.shape, int)
    for t in side["tiles"]:
        cover[t["y_int_px"] + 2: t["y_int_px"] + TH - 2, t["x_int_px"] + 2: t["x_int_px"] + TW - 2] += 1
    band = cover >= 2
    err = np.abs(m - gt)[band]
    assert err.mean() < noise, err.mean()                # ghosting would add texture-edge errors >> noise
    # compare: rounding the origins (old behaviour) is visibly worse in the bands
    tile_err = np.abs(m - gt)[cover == 1].mean()
    assert err.mean() < 1.2 * tile_err + 0.5


def test_min_difference_seam_and_difference_image(tmp_path):
    files, pos = make_tiles(tmp_path, GRID)
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), seam="min_difference"), tmp_path / "o")["BSE"]
    side = res.info
    assert side["render"]["seam"] == "min_difference" and side["render"]["min_difference_seams"] >= 5
    q = next(q for q in side["pairs"] if q["used"])
    img, st = stitching.overlap_difference(side, q["a"], q["b"])
    assert img.dtype == np.uint8 and img.ndim == 2 and min(img.shape) > 40
    assert st["misalignment_px"] < 0.2 and st["a"] == q["a"]
    raw = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), blend="none", seam="min_difference"),
                           tmp_path / "r")["BSE"]
    assert "integer" in raw.info["placement"]


def test_manual_layout_refine_and_lock(tmp_path):
    """No stage data: a rough manual arrangement (errors ~ 25 px) is refined within 10 % of the tile size; a locked
    tile keeps its manual position."""
    files, pos = make_tiles(tmp_path, GRID, txt=False)
    rng = np.random.default_rng(3)
    manual = {f.stem: (float(c + rng.uniform(-25, 25)), float(r + rng.uniform(-25, 25)))
              for f, (r, c) in zip(files, pos)}
    lock = files[2].stem
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), manual_layout=manual, locked=[lock],
                                                  refine_radius=0.1), tmp_path / "o")["BSE"]
    side = res.info
    err = _rel(side, pos, [f.stem for f in files])
    err = err - err[2]                                       # relative to the locked tile
    assert np.abs(err).max() < 1.0, err
    assert "manual layout" in side["stage_map"]["notes"][0] and side["stage_map"]["locked"] == [lock]
    assert side["params"]["manual_layout"][lock] == list(manual[lock])
    # positions outside the search radius are not found (search is limited to the radius)
    bad = dict(manual)
    bad[files[1].stem] = (manual[files[1].stem][0] + 200.0, manual[files[1].stem][1])
    res2 = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), manual_layout=bad, refine_radius=0.1),
                            tmp_path / "o2")["BSE"]
    t1 = next(t for t in res2.info["tiles"] if t["name"] == files[1].stem)
    t0 = next(t for t in res2.info["tiles"] if t["name"] == files[0].stem)
    assert abs((t1["x_px"] - t0["x_px"]) - (pos[1][1] - pos[0][1])) > 50


def test_never_mixes_detectors(tmp_path):
    make_tiles(tmp_path, GRID[:2], det="BED-C")
    make_tiles(tmp_path, GRID[:2], det="SED", seed=5)
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), detector="both"), tmp_path / "o")
    assert set(res) == {"BSE", "SE"}
    assert {t["detector"] for t in res["BSE"].info["tiles"]} == {"BED-C"}
    assert {t["detector"] for t in res["SE"].info["tiles"]} == {"SED"}
    with pytest.raises(ValueError, match="no SE images"):
        stitching.stitch(stitching.StitchParams(folder=str(tmp_path), detector="SE", exclude=["9771", "9772"]),
                         tmp_path / "p")


def test_se_placed_with_bse_layout(tmp_path):
    base = _texture(int(max(r for r, _ in GRID)) + TH + 16, int(max(c for _, c in GRID)) + TW + 16, 4)
    make_tiles(tmp_path, GRID, det="BED-C", base=base)
    # SE: same positions + a small drift, other contrast (inverted gain) and noise
    se_pos = [(r + 1.5, c - 2.0) for r, c in GRID]
    make_tiles(tmp_path, se_pos, det="SED", base=base, gains=[(0.7, 40.0)] * 6, seed=7,
               origin=np.min(np.asarray(GRID), axis=0))
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), detector="both", se_layout="bse"),
                           tmp_path / "o")
    b, s = res["BSE"].info, res["SE"].info
    assert b["mosaic_px"] == s["mosaic_px"]
    for m in s["se_to_bse"]:
        assert m["ok"] and m["shift_px"] == [pytest.approx(-2.0, abs=0.5), pytest.approx(1.5, abs=0.5)]
    bt = {t["image_number"]: t for t in b["tiles"]}
    for t in s["tiles"]:
        assert t["x_px"] - bt[t["image_number"]]["x_px"] == pytest.approx(-2.0, abs=0.5)
        assert t["placed_by"] == "BSE layout"


def test_keep_strip_single_column_only(tmp_path):
    col = [(0.0, 0.0), (381.3, 2.2), (760.6, -1.4)]
    files, pos = make_tiles(tmp_path, col)
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), info_strip="keep"), tmp_path / "o")["BSE"]
    m = tifffile.imread(res.tiff)
    st = res.info["strip"]
    assert st["mode"] == "keep" and st["height_px"] == BAR and st["image_number"] == 9773     # bottom tile
    orig = np.asarray(Image.open(files[2]))[TH:]
    x0 = st["x_offset_px"]
    np.testing.assert_array_equal(m[-BAR:, x0: x0 + TW], orig)
    assert m.shape[0] == res.info["mosaic_px"][1] + BAR
    tmp2 = tmp_path / "grid"
    tmp2.mkdir()
    make_tiles(tmp2, GRID)
    with pytest.raises(ValueError, match="single-column"):
        stitching.stitch(stitching.StitchParams(folder=str(tmp2), info_strip="keep"), tmp2 / "o")


def test_generated_strip_values_and_scale_bar(tmp_path):
    files, pos = make_tiles(tmp_path, GRID, wd=[11.2, 11.3, 11.3, 11.4, 11.3, 11.3])
    res = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), info_strip="generate"), tmp_path / "o")["BSE"]
    st = res.info["strip"]
    f = st["fields"]
    assert f["detector"] == "BED-C" and f["kv"] == "20.0 kV" and f["probe"] == "Std.-PC 60.0"
    assert f["wd"] == "WD 11.2–11.4 mm"                       # differs between tiles -> range
    assert f["mag"] == "x4,000" and f["numbers"] == "9771–9776, 6 tiles" and f["date"] == "Oct. 29 2026"
    m = tifffile.imread(res.tiff)
    strip = m[-st["height_px"]:]
    assert st["height_px"] == BAR and strip.shape[1] == res.info["mosaic_px"][0]
    bar = jeol_strip.find_scale_bar(strip)
    assert bar["length_px"] == st["bar_px"] == round(st["bar_um"] / PS)
    assert st["bar_um"] == 5.0                                 # largest round length <= SM_MICRON_BAR (100 px)


def test_nice_bar():
    assert jeol_strip.nice_bar(0.01, 500) == (5.0, 500)
    assert jeol_strip.nice_bar(0.01, 499) == (2.0, 200)
    assert jeol_strip.nice_bar(1.087, 1000) == (1000.0, 920)
    assert jeol_strip.bar_label(5) == "5 µm" and jeol_strip.bar_label(2000) == "2 mm"
    assert jeol_strip.image_numbers_text([9771, 9773, 9772]) == "9771–9773, 3 tiles"
    assert jeol_strip.image_numbers_text([1, 5]) == "1, 5 (2 tiles)"


def test_strip_date_ignores_locale():
    """The JEOL strip uses English month names whatever the process locale (a Czech LC_TIME gave 'říj.')."""
    import locale

    old = locale.setlocale(locale.LC_TIME)
    try:
        for loc in ("cs_CZ.UTF-8", "Czech_Czechia.1250", "cs-CZ"):
            try:
                locale.setlocale(locale.LC_TIME, loc)
                break
            except locale.Error:
                continue
        assert jeol_strip._jeol_date("2026-10-29") == "Oct. 29 2026"
    finally:
        locale.setlocale(locale.LC_TIME, old)


def test_tiff_pyramid(tmp_path):
    img = (np.arange(3000 * 2500) % 251).astype(np.uint8).reshape(3000, 2500)
    info = stitching.write_tiff(tmp_path / "m.tif", img, 0.01, pyramid_min_px=1024)
    assert info["pyramid_levels"] >= 2
    with tifffile.TiffFile(tmp_path / "m.tif") as tf:
        s = tf.series[0]
        assert s.levels[0].shape == img.shape and s.levels[1].shape == (1500, 1250)
        np.testing.assert_array_equal(s.levels[0].asarray(), img)
        assert tf.pages[0].tags["XResolution"].value[0] / tf.pages[0].tags["XResolution"].value[1] == \
            pytest.approx(1e4 / 0.01)


def test_op_recipe_replay(project, tmp_path):
    make_tiles(tmp_path, GRID[:4])
    step = service.run(project, "sem.stitch", {"folder": str(tmp_path), "info_strip": "generate"})
    assert {"mosaic_BSE", "preview_BSE", "sidecar_BSE", "tiles_BSE"} <= set(step.outputs)
    assert step.outputs["mosaic_BSE"].kind == "mosaic" and step.params["normalize"] is True
    assert len(step.inputs) == 8                                # 4 images + 4 .txt
    side = json.loads((project.root / step.outputs["sidecar_BSE"].path).read_text(encoding="utf-8"))
    assert side["strip"]["height_px"] == BAR and side["pixel_size_um"] == pytest.approx(PS)
    assert service.replay(project).ok
    assert service.verify_outputs(project) == []


def test_mcp_and_cli(project, tmp_path, capsys, monkeypatch):
    from ti64app import cli, mcp_server

    make_tiles(tmp_path, GRID[:2], det="BED-C")
    make_tiles(tmp_path, GRID[:2], det="SED", seed=5)
    sc = mcp_server.scan_sem_images(folder=str(tmp_path))
    assert sc["counts"] == {"BSE": 2, "SE": 2}
    monkeypatch.setattr(mcp_server, "_project", lambda name, create=False: project)
    out = mcp_server.stitch_images("t", folder=str(tmp_path), detector="both", info_strip="generate",
                                   options={"min_score": 0.2})
    assert out["BSE"]["mosaic_px"][0] >= TW and out["SE"]["strip"]["mode"] == "generate"
    assert cli.main(["stitch", str(tmp_path), "--scan"]) == 0
    assert "BSE 2, SE 2" in capsys.readouterr().err


def test_mosaic_export_formats(project, tmp_path):
    """sem.export (phase 6.6 export dialog): PNG native with scale bar + strip, width at DPI, SVG, PDF; names start
    with the sample ID."""
    from PIL import Image

    make_tiles(tmp_path, GRID[:2])
    st = service.run(project, "sem.stitch", {"folder": str(tmp_path), "info_strip": "generate", "sample_id": "JU05"})
    side = json.loads((project.root / st.outputs["sidecar_BSE"].path).read_text(encoding="utf-8"))
    assert st.sample_id == "JU05" and st.outputs["mosaic_BSE"].path.endswith("/JU05_mosaic_BSE.tif")
    e = service.run(project, "sem.export", {"stitch": st.id, "format": "png"})
    f = project.root / e.outputs["export"].path
    w, h = side["mosaic_px"]
    with Image.open(f) as im:
        assert im.width == w and im.height == h + side["strip"]["height_px"]
    assert f.name == "JU05_mosaic_BSE.png"
    e2 = service.run(project, "sem.export", {"stitch": st.id, "format": "tiff", "mode": "width", "width_mm": 50,
                                             "dpi": 300, "info_strip": False})
    meta = json.loads((project.root / e2.outputs["sidecar"].path).read_text(encoding="utf-8"))
    assert meta["image_px"][0] == round(50 / 25.4 * 300) and not meta["info_strip"]
    for fmt in ("svg", "pdf"):
        e3 = service.run(project, "sem.export", {"stitch": st.id, "format": fmt, "scale_bar": False})
        assert (project.root / e3.outputs["export"].path).stat().st_size > 1000
    assert service.replay(project).ok


# ---- fix 2 of the 6.6 user test: stage axes that are not recorded (JU04), repeats --------------------------------
def _flatten_stage(folder, y_mm=0.237, x_by_num=None):
    """Rewrite the .txt files like JU04: stage Y the same in every image (and X per column if given)."""
    import re
    for f in folder.glob("*.txt"):
        s = f.read_text(encoding="utf-8")
        num = int(re.search(r"\$SM_IMAGE_NUMBER (\d+)", s).group(1))
        x, y = (float(v) for v in re.search(r"\$CM_STAGE_POSITION ([\d.]+) ([\d.]+)", s).groups())
        x = x_by_num.get(num, x) if x_by_num else x
        f.write_text(re.sub(r"\$CM_STAGE_POSITION [\d.]+ [\d.]+", f"$CM_STAGE_POSITION {x:.3f} {y_mm:.3f}", s),
                     encoding="utf-8")


JU04_LIKE = [(0.0, 0.0), (381.3, 2.4), (14.6, 405.2), (393.8, 401.7)]    # two columns of two, ~20 % overlap


def test_stage_analysis_repeats_no_unknown_axis(tmp_path):
    """Fix 3.17: a constant stage axis is no longer 'unknown' (no warning, no spreading); identical positions and
    possible repeats are still listed."""
    make_tiles(tmp_path, JU04_LIKE)
    # columns: same stage X per column (as JU04: images of one column share the position), Y constant
    _flatten_stage(tmp_path, x_by_num={9771: 1.0, 9772: 1.0, 9773: 0.980, 9774: 0.980})
    tl = stitching.scan(tmp_path)
    sa = stitching.stage_analysis(tl)
    assert "unknown_axes" not in sa and "message" not in sa and sa["spread_um"][1] == 0.0
    assert sa["identical"] == [["9771", "9772"], ["9773", "9774"]]
    assert sa["repeats"] == [["9771", "9772"], ["9773", "9774"]]
    assert any("possible repeats" in q for q in sa["notes"])
    # the Arrange preview puts the tiles at their stage positions (no spreading along an axis)
    lay = stitching.layout_preview(tl)
    ys = {t["image_number"]: t["y"] for t in lay["tiles"]}
    assert ys[9772] == ys[9771] and lay["source"] == "stage"
    # without stage use: a grid in image-number order, no overlaps
    g = stitching.layout_preview(tl, use_stage=False)
    pos = {t["image_number"]: (t["x"], t["y"]) for t in g["tiles"]}
    assert len(set(pos.values())) == 4 and "not used" in g["source"]
    # stitch sets: a faulty stage does not split the set when the stage is not used
    assert len(stitching.stitch_sets(tl, use_stage=False)) == 1


def test_no_stage_positions_by_matching(tmp_path):
    """'Do not use stage positions' (fix 3.17): placement only from image matching of all pairs; JU04-like faulty
    stage (Y constant, identical positions per column) is placed within a pixel."""
    files, pos = make_tiles(tmp_path, JU04_LIKE)
    _flatten_stage(tmp_path, x_by_num={9771: 1.0, 9772: 1.0, 9773: 0.980, 9774: 0.980})
    side = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), use_stage=False), tmp_path / "o")["BSE"].info
    err = _rel(side, pos, [f.stem for f in files])
    assert np.abs(err).max() < 1.0, err
    assert side["stage_only_tiles"] == [] and side["misalignment_px"]["max"] < 0.5
    notes = " ".join(side["notes"])
    assert "stage positions not used" in notes and "possible repeats" in notes and "not repeats" in notes
    assert "not in the metadata" not in notes and side["stage_to_mosaic"] is None
    # with the (faulty) stage positions the rows land on top of each other: no automatic full-range search any more
    old = stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "o2")
    assert np.abs(_rel(old["BSE"].info, pos, [f.stem for f in files])).max() > 100
    assert "not in the metadata" not in " ".join(old["BSE"].info["notes"])


def test_repeat_confirmed(tmp_path):
    """Two images of the same field (same stage position and magnification) are confirmed as a repeat."""
    make_tiles(tmp_path, [(0.0, 0.0), (0.4, -0.3)], seed=4)
    _flatten_stage(tmp_path, x_by_num={9771: 1.0, 9772: 1.0})
    side = stitching.stitch(stitching.StitchParams(folder=str(tmp_path), use_stage=False), tmp_path / "o")["BSE"].info
    assert any("repeat confirmed" in q and "9771 = 9772" in q for q in side["notes"])


def test_seam_lines_saved(tmp_path):
    """The seam lines of a stitch are saved for the Result viewer (fix 3.16): between tiles only, inside the canvas."""
    make_tiles(tmp_path, JU04_LIKE)
    r = stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "o")["BSE"]
    assert r.seams is not None and r.seams.is_file()
    with np.load(r.seams) as z:
        assert z["x1"].size > 0
        W, H = r.info["mosaic_px"]
        assert z["x2"].max() <= W and z["y2"].max() <= H and z["x1"].min() > 0


def test_export_estimate_and_guard():
    from ti64app.ops_stitch import export_estimate

    ok = export_estimate((5000, 4000), (5000, 4000), "png")
    assert ok["level"] == "ok" and ok["ok"]
    big = export_estimate((9767, 22453), (9767, 22453), "png")         # the 6.6 user test mosaic: works, warned
    assert big["level"] == "warn" and big["ok"] and "BigTIFF" in big["message"]
    huge = export_estimate((40000, 60000), (40000, 60000), "png")
    assert huge["level"] == "refuse" and not huge["ok"]
    assert [a["fraction"] for a in huge["alternatives"]] == ["1/2", "1/4", "1/8"]
    assert export_estimate((30000, 40000), (30000, 40000), "bigtiff")["ok"]
    assert export_estimate((40000, 30000), (40000, 30000), "tiff")["level"] == "refuse"     # > 4 GB classic TIFF


def test_png_hash_large_image_streamed(tmp_path):
    """Output hashes of images above PIL's decompression-bomb limit (the cause of the failed mosaic export); the
    streamed hash equals the old full-array hash."""
    from ti64app.hashing import array_hash, png_hash

    a = np.random.default_rng(0).integers(0, 255, (300, 700), dtype=np.uint8)
    f = tmp_path / "a.png"
    Image.fromarray(a).save(f)
    assert png_hash(f) == array_hash({"rgba": np.asarray(Image.open(f).convert("RGBA"))})
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1000                       # pretend the image is above the limit
    try:
        assert png_hash(f).startswith("sha256:")
    finally:
        Image.MAX_IMAGE_PIXELS = old


def test_mosaic_export_fractions_bigtiff_one_scale_bar(project, tmp_path, monkeypatch):
    """Fix 15-17: 1/2, 1/4, 1/8 of the original; tiled BigTIFF; with the (generated) info strip the viewer's scale
    bar is left out (only the strip's own bar); a size that does not fit in memory is refused BEFORE the export."""
    import tifffile
    from PIL import Image

    from ti64app import ops_stitch

    make_tiles(tmp_path, GRID[:2])
    st = service.run(project, "sem.stitch", {"folder": str(tmp_path), "info_strip": "generate"})
    side = json.loads((project.root / st.outputs["sidecar_BSE"].path).read_text(encoding="utf-8"))
    w, h = side["mosaic_px"]
    sh = side["strip"]["height_px"]
    bar = [{"kind": "scale_bar", "x": 0.03, "y": 0.8, "w": 0.2}]
    for k in (2, 4, 8):
        e = service.run(project, "sem.export", {"stitch": st.id, "mode": "scale", "scale": 1 / k, "legends": bar,
                                                "info_strip": False})
        meta = json.loads((project.root / e.outputs["sidecar"].path).read_text(encoding="utf-8"))
        assert meta["image_px"] == [-(-w // k), -(-h // k)] and abs(meta["pixel_size_um"] - PS * k) < 1e-9
        assert meta["scale_bar"] is True and meta["scale_bar_source"] == "image"
    # with the info strip: one scale bar only (the strip's); the image area is untouched by the viewer's bar
    a = service.run(project, "sem.export", {"stitch": st.id, "legends": bar, "info_strip": True})
    b = service.run(project, "sem.export", {"stitch": st.id, "legends": [], "info_strip": True})
    meta = json.loads((project.root / a.outputs["sidecar"].path).read_text(encoding="utf-8"))
    assert meta["scale_bar_left_out"] and meta["scale_bar_source"] == "info strip" and meta["legends"] == []
    with Image.open(project.root / a.outputs["export"].path) as ia, Image.open(project.root / b.outputs["export"].path) as ib:
        assert np.array_equal(np.asarray(ia), np.asarray(ib)) and ia.height == h + sh
    # tiled BigTIFF
    t = service.run(project, "sem.export", {"stitch": st.id, "format": "bigtiff", "info_strip": False})
    f = project.root / t.outputs["export"].path
    with tifffile.TiffFile(f) as tf:
        assert tf.is_bigtiff and tf.pages[0].is_tiled and tf.pages[0].shape == (h, w)
    # refused before anything is written, with the alternatives in the message
    monkeypatch.setattr(ops_stitch, "MEMORY_BUDGET", 1000)
    n = len(project.recipe().steps)
    with pytest.raises(ValueError, match=r"export not possible: .*1/2, 1/4, 1/8.*BigTIFF"):
        service.run(project, "sem.export", {"stitch": st.id, "format": "png"})
    assert len(project.recipe().steps) == n
