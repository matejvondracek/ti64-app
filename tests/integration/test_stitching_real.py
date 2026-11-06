"""SEM stitching on the real JEOL JSM-IT500HR images (git-ignored; skipped when missing):
data/test/stitch (8 stage positions x BSE "BED-C" + SE "SED", 2560 x 2048 .bmp, 5000x, 10 nm/px) and the data-bar
crop at all three resolutions on data/JU04-JU06. Stitching runs are marked slow."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

from ti64app.io import bse as bse_io
from ti64app.modules import jeol_strip, stitching

REPO = Path(__file__).resolve().parents[2]
STITCH = REPO / "data" / "test" / "stitch"
JU = [REPO / "data" / d for d in ("JU04", "JU05", "JU06")]
EXPECTED_STRIP = {1280: 64, 2560: 128, 5120: 256}


@pytest.fixture(scope="module")
def stitch_dir() -> Path:
    if not STITCH.is_dir() or not list(STITCH.glob("*.bmp")):
        pytest.skip("data/test/stitch missing (git-ignored)")
    return STITCH


@pytest.fixture(scope="module")
def tiles(stitch_dir):
    return stitching.scan(stitch_dir)


def test_detector_filter_8_bed_8_sed(tiles):
    assert stitching.detector_counts(tiles) == {"BSE": 8, "SE": 8}
    assert stitching.detector_detail(tiles) == {"BED-C": 8, "SED": 8}
    assert {t.detector_source for t in tiles} == {"txt"}
    for t in tiles:                       # the file name agrees with $SM_DETECTOR
        assert stitching.detector_from_filename(t.name) == t.detector


def test_crop_pixel_size_and_metadata(tiles):
    for t in tiles:
        assert (t.file_width, t.file_height) == (2560, 2048)
        assert t.strip_px == 128 and t.strip_source == "txt" and t.height == 1920
        assert t.pixel_size_um == pytest.approx(0.010)           # 25.60 um / 2560 px
        assert t.magnification == 5000 and t.scan_rotation_deg == 0 and t.stage_rotation_deg == pytest.approx(241.12)
        assert t.beam_shift is not None
    m = bse_io.read_metadata(tiles[0].txt, image_shape=(2048, 2560))
    assert m.verified_format and m.field_of_view_um == pytest.approx((25.6, 19.2))
    assert m.stage_x_mm == pytest.approx(0.923) and m.stage_tilt_deg == 0.0


def test_strip_detection_confirms_txt(tiles):
    for t in tiles[:4]:
        img = bse_io.read_image(t.path)
        assert stitching.detect_strip(img) == 128
        assert stitching.detect_strip(img, expected=2048 - 2560 * 3 // 4) == 128     # 4:3 rule


def test_crop_at_three_resolutions():
    files = [f for d in JU if d.is_dir() for f in d.rglob("*.bmp")]
    if not files:
        pytest.skip("data/JU04-JU06 missing (git-ignored)")
    seen: dict[int, set] = {}
    checked_detect = set()
    for f in files:
        t = stitching.read_tile(f)
        assert t.strip_source == "txt", f
        assert t.strip_px == EXPECTED_STRIP[t.file_width], f
        assert t.file_height - t.strip_px == t.file_width * 3 // 4                     # JEOL image area 4:3
        assert t.strip_px == int(bse_io.parse_txt(Path(t.txt).read_text(encoding="utf-8-sig"))["SM_PNU_HEIGHT"])
        seen.setdefault(t.file_width, set()).add(t.strip_px)
        # image-based check: the 4:3 rule + strip detection (the no-.txt path) finds the same height
        if len(checked_detect) < 60 or t.file_width not in {w for w, _ in checked_detect}:
            assert stitching.detect_strip(bse_io.read_image(f), expected=t.file_height - t.file_width * 3 // 4) ==                 t.strip_px, f
            checked_detect.add((t.file_width, f.name))
    assert seen == {1280: {64}, 2560: {128}, 5120: {256}}


def test_generated_strip_layout_matches_original(tiles):
    """A strip drawn from one tile's own metadata lines up with JEOL's: same height, scale bar at the same pixels and
    of the same length (5 um = 500 px), text rows at the same heights, fields starting at the same columns."""
    t = next(t for t in tiles if t.image_number == 9771 and t.detector_class == "BSE")
    orig = bse_io.read_image(t.path)[t.height:]
    gen, info = jeol_strip.render_strip(jeol_strip.tile_fields(t), 2560, 128, t.pixel_size_um, 2560,
                                        jeol_strip.max_bar_px([t]))
    assert gen.shape == orig.shape == (128, 2560)
    bo, bg = jeol_strip.find_scale_bar(orig), jeol_strip.find_scale_bar(gen)
    assert bo["length_px"] == 500 == t.micron_bar_px
    for k in ("x0", "x1", "y0", "y1"):
        assert abs(bo[k] - bg[k]) <= 1, (k, bo, bg)
    assert info["bar_um"] == 5.0 and info["bar_label"] == "5 µm"
    for (a0, a1), (b0, b1) in zip(jeol_strip.text_rows(orig), jeol_strip.text_rows(gen)):
        assert abs(a0 - b0) <= 3 and abs(a1 - b1) <= 3
    so, sg = jeol_strip.group_starts(orig, 0), jeol_strip.group_starts(gen, 0)
    # detector, kV, WD, Std.-PC, camera, scale bar (+ label)
    for x in so[:5]:
        assert min(abs(x - y) for y in sg) <= 8, (so, sg)
    r2o, r2g = jeol_strip.group_starts(orig, 1), jeol_strip.group_starts(gen, 1)
    assert abs(r2o[0] - r2g[0]) <= 4 and abs(r2o[1] - r2g[1]) <= 4                 # image number, date
    f = jeol_strip.tile_fields(t)
    assert (f["detector"], f["kv"], f["wd"], f["probe"], f["mag"], f["numbers"], f["date"]) == \
        ("BED-C", "20.0 kV", "WD 11.3 mm", "Std.-PC 60.0", "x5,000", "9771", "Oct. 29 2026")


@pytest.fixture(scope="module")
def stitched(stitch_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("stitch")
    both = stitching.stitch(stitching.StitchParams(folder=str(stitch_dir), detector="both", se_layout="bse",
                                                   info_strip="keep"), out / "keep")
    gen = stitching.stitch(stitching.StitchParams(folder=str(stitch_dir), detector="BSE", info_strip="generate"),
                           out / "gen")
    se_own = stitching.stitch(stitching.StitchParams(folder=str(stitch_dir), detector="SE"), out / "se")
    return both, gen["BSE"], se_own["SE"]


@pytest.mark.slow
def test_column_in_stage_order(stitched, tiles):
    side = stitched[0]["BSE"].info
    assert side["n_tiles"] == 8 and side["stage_only_tiles"] == []
    assert all(q["used"] for q in side["pairs"] if q["ok"]) and sum(q["used"] for q in side["pairs"]) == 7
    assert side["residual_px"]["max"] < 1.0
    ys = {t["image_number"]: t["y_px"] for t in side["tiles"]}
    stage_y = {t.image_number: t.stage_y_mm for t in tiles if t.detector_class == "BSE"}
    order_img = sorted(ys, key=ys.get)                         # top -> bottom in the mosaic
    order_stage = sorted(stage_y, key=stage_y.get)             # increasing stage Y
    assert order_img == order_stage == [9776, 9775, 9774, 9773, 9772, 9771, 9777, 9778]
    # +stage Y = +row (down), +stage X = -col (measured, decisions D17)
    assert side["stage_map"]["chosen"] == [[-1, 0], [0, 1]]
    w, h = side["mosaic_px"]
    assert 2560 <= w <= 2560 + 128 and h == pytest.approx(1920 + (20.470 - 20.370) * 1e3 / 0.010, abs=150)
    m = tifffile.imread(stitched[0]["BSE"].tiff)
    assert m.shape == (h + 128, w) and m.dtype == np.uint8


@pytest.mark.slow
def test_kept_strip_is_bottom_tile(stitched, stitch_dir):
    r = stitched[0]["BSE"]
    st = r.info["strip"]
    assert st["mode"] == "keep" and st["height_px"] == 128 and st["image_number"] == 9778
    orig = np.asarray(Image.open(next(stitch_dir.glob("9778_*BED-C*.bmp"))))[1920:]
    m = tifffile.imread(r.tiff)
    x0 = st["x_offset_px"]
    np.testing.assert_array_equal(m[-128:, x0: x0 + 2560], orig)
    assert jeol_strip.find_scale_bar(m[-128:])["length_px"] == 500
    assert r.info["mosaic_px"][1] + 128 == m.shape[0]           # analysis pixels = mosaic minus strip


@pytest.mark.slow
def test_generated_strip_on_mosaic(stitched):
    r = stitched[1]
    st = r.info["strip"]
    f = st["fields"]
    assert f["detector"] == "BED-C" and f["kv"] == "20.0 kV" and f["wd"] == "WD 11.3 mm"
    assert f["probe"] == "Std.-PC 60.0" and f["mag"] == "x5,000"
    assert f["numbers"] == "9771–9778, 8 tiles" and f["date"] == "Oct. 29 2026"
    m = tifffile.imread(r.tiff)
    bar = jeol_strip.find_scale_bar(m[-128:])
    assert st["bar_um"] == 5.0 and bar["length_px"] == 500 == round(5.0 / r.info["pixel_size_um"])


@pytest.mark.slow
def test_bse_and_se_mosaics_align(stitched):
    b, s = stitched[0]["BSE"].info, stitched[0]["SE"].info
    assert s["mosaic_px"] == b["mosaic_px"]
    good = [m for m in s["se_to_bse"] if m["ok"]]
    assert len(good) >= 6
    assert all(abs(m["shift_px"][0]) < 3 and abs(m["shift_px"][1]) < 3 for m in good)
    assert all(0 < m["seconds_before_bse"] < 60 for m in s["se_to_bse"])       # SE scanned ~15-40 s before BSE
    # SE stitched on its own lands where the BSE tiles are (relative positions within 1.5 px)
    own = {t["image_number"]: (t["x_px"], t["y_px"]) for t in stitched[2].info["tiles"]}
    bse = {t["image_number"]: (t["x_px"], t["y_px"]) for t in b["tiles"]}
    d = np.array([np.subtract(own[k], bse[k]) for k in bse])
    assert np.abs(d - d.mean(axis=0)).max() < 1.5


@pytest.mark.slow
def test_stage_x_sign_from_ju05_overview():
    """The JU05 50x overview (stage moved along X only, same stage rotation) confirms +stage X -> -col."""
    folder = REPO / "data" / "JU05"
    if not folder.is_dir():
        pytest.skip("data/JU05 missing")
    tl = [t for t in stitching.scan(files=sorted(folder.glob("*BED*.bmp")))
          if t.magnification == 50 and t.width == 2560 and t.stage_rotation_deg == pytest.approx(241.12)]
    if len(tl) < 2:
        pytest.skip("JU05 50x tiles not found")
    store = stitching.TileStore(tl)
    M, rep = stitching.choose_stage_map(tl, store, tl[0].pixel_size_um,
                                        stitching.StitchParams(min_overlap_px=200, search_um=150), 4)
    assert [list(r) for r in M][0] == [-1, 0]
    scores = {tuple(map(tuple, c["map"])): c["mean_score"] for c in rep["candidates"]}
    assert scores[((-1, 0), (0, 1))] > 0.6 > scores[((1, 0), (0, 1))]
