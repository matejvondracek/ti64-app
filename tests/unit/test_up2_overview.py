"""Overview of .up2 pattern files (fix 13): IQ = kikuchipy's definition, streaming pass, subsampling, cache, cancel
and resume, hex grids. Synthetic files only (the real 43 GB file is timed by hand, docs/decisions.md D23)."""
import struct
import threading

import numpy as np
import pytest

from ti64app.io import up2
from ti64app.modules import up2_overview as uo


def _write_up2(path, n_cols, n_rows, w=12, h=10, hexgrid=False, seed=0):
    hd = up2.Up2Header(4, w, h, 42, n_cols, n_rows, hexgrid, 0.5, 0.5, 0)
    n = hd.n_patterns
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:h, :w]
    pats = np.empty((n, h, w), "<u2")
    for i in range(n):            # bands of varying sharpness + noise: IQ varies from pattern to pattern
        band = np.cos(xx * (0.3 + 0.05 * (i % 7)) + yy * 0.2 * (i % 3)) * (200 + 30 * (i % 5))
        pats[i] = np.clip(1000 + band + rng.normal(0, 20 + 10 * (i % 4), (h, w)), 0, 65535)
    path.write_bytes(struct.pack("<4i", 4, w, h, 42) + struct.pack("<BiiBdd", 0, n_cols, n_rows, int(hexgrid), 0.5, 0.5)
                     + pats.tobytes())
    return pats


def test_iq_matches_kikuchipy():
    kp = pytest.importorskip("kikuchipy")
    rng = np.random.default_rng(1)
    for h, w in ((10, 12), (9, 11), (60, 60)):
        pats = rng.integers(0, 4000, (5, h, w)).astype(np.uint16)
        pats[2] += (np.cos(np.arange(w) * 0.4) * 900 + 1000).astype(np.uint16)[None, :]
        ours = uo.pattern_quality(pats)
        ref = [kp.pattern.get_image_quality(p.astype(np.float32)) for p in pats]
        np.testing.assert_allclose(ours, ref, rtol=1e-4, atol=1e-5)


def test_overview_full_and_subsampled(tmp_path):
    f = tmp_path / "s.up2"
    pats = _write_up2(f, 7, 5)
    ov = uo.compute(f, tmp_path / "cache", step=1)
    assert ov.complete and ov.iq.shape == (5, 7)
    np.testing.assert_allclose(ov.vbse.ravel(), pats.reshape(len(pats), -1).mean(axis=1), rtol=1e-6)
    np.testing.assert_allclose(ov.background, pats.mean(axis=0), rtol=1e-5)       # all 35 patterns sampled
    np.testing.assert_allclose(ov.iq.ravel(), uo.pattern_quality(pats, ov.background, 12 / 8), rtol=1e-5)
    assert ov.image("vbse", flatten=True).shape == (5, 7)
    ov2 = uo.compute(f, tmp_path / "cache", step=2)
    assert ov2.iq.shape == (3, 4) and ov2.pixel_um == (1.0, 1.0)
    np.testing.assert_allclose(ov2.iq, ov.iq[::2, ::2], rtol=1e-6)
    assert ov2.scan_index(2, 3) == (4, 6)
    assert uo.load_cached(tmp_path / "cache", f, 2) is not None          # cached
    assert uo.compute(f, tmp_path / "cache", step=2).seconds == ov2.seconds   # returned from the cache
    assert ov.image("iq").dtype == np.uint8 and ov.image("iq").shape == (5, 7)


def test_cancel_and_resume(tmp_path, monkeypatch):
    f = tmp_path / "c.up2"
    _write_up2(f, 6, 9)
    monkeypatch.setattr(uo, "BATCH_PATTERNS", 12)          # 2 rows per batch
    ev = threading.Event()
    seen = []

    def prog(fr, txt):
        seen.append(fr)
        ev.set()                                          # cancel after the first batch
    with pytest.raises(uo.Cancelled):
        uo.compute(f, tmp_path / "cache", 1, prog, ev)
    part = uo.load_cached(tmp_path / "cache", f, 1)
    assert part is not None and part.rows_done == 2 and not part.complete
    assert np.isnan(part.iq[2:]).all() and np.isfinite(part.iq[:2]).all()
    full = uo.compute(f, tmp_path / "cache", 1)
    ref = uo.compute(f, tmp_path / "cache2", 1)
    assert full.complete
    np.testing.assert_allclose(full.iq, ref.iq)


def test_hex_grid_and_cache_key(tmp_path):
    f = tmp_path / "h.up2"
    pats = _write_up2(f, 5, 4, hexgrid=True)
    hd = up2.read_header(f)
    ov = uo.compute(f, tmp_path / "cache", 1)
    assert ov.iq.shape == (4, 5)
    assert np.isnan(ov.iq[1, 4]) and np.isfinite(ov.iq[1, :4]).all()       # odd rows are one pattern shorter
    np.testing.assert_allclose(ov.vbse[1, 2], pats[up2.pattern_index(hd, 1, 2)].mean(), rtol=1e-6)
    k1 = uo.cache_path(tmp_path / "cache", f, 1)
    f.write_bytes(f.read_bytes())                                           # new mtime -> new key
    import os
    os.utime(f, ns=(1, 1))
    assert uo.cache_path(tmp_path / "cache", f, 1) != k1


def test_background_job(tmp_path):
    f = tmp_path / "j.up2"
    _write_up2(f, 4, 4)
    j = uo.start_job(f, tmp_path / "cache", 2)
    for _ in range(200):
        if j.done:
            break
        threading.Event().wait(0.02)
    assert j.done and j.error is None and j.result.complete and j.fraction == 1.0


def test_corrected_pattern_and_mcp(tmp_path, monkeypatch):
    f = tmp_path / "m.up2"
    pats = _write_up2(f, 4, 3)
    bg = pats.mean(axis=0).astype(np.float32)
    c = uo.corrected_pattern(pats[5], bg)
    assert c.shape == pats[5].shape and abs(float(c.mean())) < 5
    from ti64app import mcp_server
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(tmp_path / "projects"))
    mcp_server._project("p", create=True)
    out = mcp_server.up2_overview(project="p", file=str(f), step=1, channel="vbse")
    assert out["shape_rows_cols"] == [3, 4] and out["pixel_um"] == [0.5, 0.5]
    from pathlib import Path
    assert Path(out["png"]).is_file()
