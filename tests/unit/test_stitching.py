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
    with pytest.raises(ValueError, match="magnifications differ"):
        stitching.stitch(stitching.StitchParams(folder=str(tmp_path)), tmp_path / "o")


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
