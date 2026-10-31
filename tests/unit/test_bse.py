"""JEOL BSE loader on a SYNTHETIC image + .txt (no real JSM-IT500HR pair yet, decisions D5)."""
import json

import numpy as np
import pytest
import tifffile

from ti64app import service
from ti64app.io import bse

TXT = """$CM_FORMAT JEOL/SEM
$CM_VERSION 1.0
$CM_INSTRUMENT JSM-IT500HR
$CM_SIGNAL BED-C
$CM_IMAGE_NAME test.tif
$CM_FULL_SIZE 1280 960
$CM_ACCEL_VOLT 20.00
$CM_MAG 500
$CM_STAGE_POS 37.5085 41.2210 10.0000 0.0 15.5
$SM_WD 10.2
$SM_MICRON_BAR 128
$SM_MICRON_MARKER 50um
$CM_DATE 2026/10/30
"""


@pytest.fixture
def bse_pair(tmp_path):
    img = (np.arange(640 * 480) % 251).astype(np.uint8).reshape(480, 640)
    tifffile.imwrite(tmp_path / "test.tif", img)
    (tmp_path / "test.txt").write_text(TXT, encoding="latin-1")
    return tmp_path / "test.tif", img


def test_parse_metadata(bse_pair):
    path, img = bse_pair
    im, m = bse.load_bse(path)
    np.testing.assert_array_equal(im, img)
    assert m.instrument == "JSM-IT500HR" and m.signal == "BED-C"
    assert (m.stage_x_mm, m.stage_y_mm, m.stage_z_mm, m.stage_tilt_deg, m.stage_rotation_deg) == \
           (37.5085, 41.221, 10.0, 0.0, 15.5)
    assert m.working_distance_mm == 10.2 and m.accelerating_voltage_kV == 20.0 and m.magnification == 500
    # 50 um / 128 px at full size 1280 px; image saved at 640 px -> x2
    assert m.pixel_size_um == pytest.approx(50 / 128 * 2)
    assert "SM_MICRON_MARKER/SM_MICRON_BAR" in m.pixel_size_source
    assert m.verified_format is False


@pytest.mark.parametrize("line,expect", [("$SM_PIXEL_SIZE 0.25", 0.25), ("$$SM_PIXEL_SIZE 250 nm", 0.25),
                                         ("$CM_PIXEL_SIZE 0.0005 mm", 0.5)])
def test_explicit_pixel_size_and_units(tmp_path, line, expect):
    f = tmp_path / "a.txt"
    f.write_text(TXT + line + "\n$CM_ACCEL_VOLT 15000\n", encoding="latin-1")
    m = bse.read_metadata(f)
    assert m.pixel_size_um == pytest.approx(expect)
    assert m.accelerating_voltage_kV == 15.0          # value in V converted to kV


def test_missing_sidecar(tmp_path):
    tifffile.imwrite(tmp_path / "x.tif", np.zeros((4, 4), np.uint8))
    with pytest.raises(FileNotFoundError):
        bse.load_bse(tmp_path / "x.tif")


def test_bse_op_and_replay(project, bse_pair):
    path, _ = bse_pair
    step = service.run(project, "bse.load", {"image": str(path)})
    meta = json.loads((project.root / step.outputs["meta"].path).read_text())
    assert meta["magnification"] == 500 and len(step.inputs) == 2
    assert service.replay(project).ok
