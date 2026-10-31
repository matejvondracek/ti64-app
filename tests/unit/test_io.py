import json
import struct

import numpy as np
import pytest

from ti64app.io import ang, up2


def test_ang_header_crop(crop300):
    h = ang.read_header(crop300)
    assert h.is_hex and h.step_um == pytest.approx(0.2)
    assert h.n_points == 90000
    assert {p["MaterialName"] for p in h.phases} == {"Ti (alpha)", "Ti (beta)"}
    # PRIAS declared in the header, absent in the data (docs/data_audit.md)
    assert h.declares_prias and len(h.declared_columns) == 13
    assert ang.data_column_count(crop300) == {10}


def test_crop_provenance(crop300, crop1000):
    for f, n in ((crop300, 300), (crop1000, 1000)):
        meta = json.loads(f.with_suffix(".json").read_text())
        assert meta["n_points"] == n * n and meta["size"] == n
        assert ang.read_header(f).n_points == n * n


def _write_up2(path, n_cols=3, n_rows=2, w=4, h=5, hexgrid=False):
    n = n_cols * n_rows
    hdr = struct.pack("<4i", 4, w, h, 42) + struct.pack("<BiiBdd", 0, n_cols, n_rows, int(hexgrid), 0.5, 0.5)
    pats = np.arange(n * w * h, dtype="<u2").reshape(n, h, w)
    path.write_bytes(hdr + pats.tobytes())
    return pats


def test_up2_synthetic(tmp_path):
    f = tmp_path / "t.up2"
    pats = _write_up2(f)
    hd = up2.read_header(f)
    assert (hd.version, hd.pattern_width, hd.pattern_height, hd.n_patterns) == (4, 4, 5, 6)
    assert hd.size_ok
    np.testing.assert_array_equal(up2.read_patterns(f, 2, 3, hd), pats[2:5])
    with pytest.raises(IndexError):
        up2.read_patterns(f, 5, 2, hd)


def test_up2_real_header(up2_file):
    hd = up2.read_header(up2_file)
    assert (hd.version, hd.pattern_width, hd.pattern_height) == (4, 60, 60)
    assert (hd.n_cols, hd.n_rows, hd.hex_grid) == (3001, 2001, False)
    assert hd.n_patterns == 6_005_001 and hd.size_ok
    p = up2.read_patterns(up2_file, 0, 2, hd)          # 2 patterns only, memory-mapped
    assert p.shape == (2, 60, 60) and p.dtype == np.uint16
