"""Phase 2 conformance: pymtex vs MATLAB/MTEX 7.2 for every operation, on all three 300x300 crops.

Tolerances (measured, docs/decisions.md D7-D8):
  cleanup      identical phases, flags and report counts; orientations <= 1e-4 deg
  grains       identical partition of indexed pixels, grain count, edge grains; ECD sum rel. 1e-3
               (not-indexed pixels near voids may join a different grain: alpha fill differs)
  boundaries   segment count rel. 1e-3; length per class (LAGB/HAGB/interface/Burgers types) rel. 5e-3
               (identical on maps without remaining voids: JU02, RM02 crops)
  KAM/GROD/GOS per pixel |diff| <= 1e-4 deg
  GND ('1hop') per pixel rel. diff <= 1e-4 (median), same NaN pattern
  IPF colours  all 4 keys, alpha + beta, |diff| <= 1e-3
  figures      both backends write a PNG for every figure kind (images are not compared)
"""
import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import CleanupParams, GrainParams, LoadParams, MapParams, RenderSpec, cleanup_params_for
from ti64app.ops_ebsd import BoundaryParams, classify_boundaries

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


@pytest.fixture
def both(matlab, crop300):
    py = get_backend("pymtex")
    lp = LoadParams(file=crop300, input_state="oim_cleaned")
    return {"pymtex": (py, py.load_ebsd(lp)), "matlab": (matlab, matlab.load_ebsd(lp))}


def rot_deg(a, b):
    m = ~np.isnan(a[:, 0])
    return np.degrees(2 * np.arccos(np.abs(np.sum(a[m] * b[m], 1)).clip(0, 1))).max() if m.any() else 0.0


@pytest.mark.parametrize("params", [cleanup_params_for("oim_cleaned"), cleanup_params_for("raw"),
                                    CleanupParams(ci_threshold=0.1, spike_removal=True)],
                         ids=["oim_cleaned", "raw", "raw+spikes"])
def test_cleanup(both, params):
    out = {}
    for n, (be, h) in both.items():
        h2, rep = be.cleanup(h, params)
        out[n] = (be.get_ebsd(h2), rep)
    (a, ra), (b, rb) = out["pymtex"], out["matlab"]
    assert ra.model_dump(exclude={"params"}) == rb.model_dump(exclude={"params"})
    np.testing.assert_array_equal(a.phase, b.phase)
    np.testing.assert_array_equal(a.props["cleanup"], b.props["cleanup"].astype(a.props["cleanup"].dtype))
    np.testing.assert_array_equal(np.isnan(a.quat[:, 0]), np.isnan(b.quat[:, 0]))
    assert rot_deg(a.quat, b.quat) < 1e-4


@pytest.fixture
def grains(both):
    out = {}
    for n, (be, h) in both.items():
        h2, _ = be.cleanup(h, cleanup_params_for("oim_cleaned"))
        gh = be.calc_grains(h2, GrainParams())
        out[n] = (be, gh, be.get_grains(gh), be.get_ebsd(h2))
    return out


def test_grains_and_boundaries(grains):
    (_, _, a, ea), (_, _, b, _) = grains["pymtex"], grains["matlab"]
    assert len(a.grain["id"]) == len(b.grain["id"])
    # identical partition of the INDEXED pixels; the grain a not-indexed pixel near a void is assigned
    # to by calcGrains' alpha fill differs between the engines (decisions D7)
    idx = ea.phase > 0
    pa, pb = a.pixel_grain_id[idx], b.pixel_grain_id[idx]
    pairs = set(zip(pa.tolist(), pb.tolist()))
    assert len(pairs) == len(set(pa.tolist())) == len(set(pb.tolist()))
    assert a.grain["is_edge"].sum() == b.grain["is_edge"].sum()
    n_unidx = int((~idx).sum())
    assert abs(int(a.grain["n_px"].sum()) - int(b.grain["n_px"].sum())) <= n_unidx
    ea_, eb_ = np.sort(a.grain["ecd_um"]), np.sort(b.grain["ecd_um"])
    assert np.abs(ea_ - eb_).max() < 0.5 and abs(ea_.sum() - eb_.sum()) / eb_.sum() < 1e-3
    na, nb = len(a.boundary["x1"]), len(b.boundary["x1"])
    assert abs(na - nb) <= 1e-3 * nb                      # segments next to voids may be split differently
    assert abs(int(a.boundary["inner"].sum()) - int(b.boundary["inner"].sum())) <= 1e-2 * b.boundary["inner"].sum()
    ca = classify_boundaries(a.boundary, GrainParams(), BoundaryParams())
    cb = classify_boundaries(b.boundary, GrainParams(), BoundaryParams())
    for k in ca:
        la, lb = a.boundary["length_um"][ca[k]].sum(), b.boundary["length_um"][cb[k]].sum()
        assert la == pytest.approx(lb, rel=5e-3, abs=1e-3), k


@pytest.mark.parametrize("mp", [MapParams(kind="kam"), MapParams(kind="kam", kam_order=3, kam_threshold_deg=3),
                                MapParams(kind="grod"), MapParams(kind="gos"), MapParams(kind="gnd")],
                         ids=["kam1", "kam3", "grod", "gos", "gnd"])
def test_orientation_maps(grains, mp):
    v = {n: be.compute_map(gh, mp) for n, (be, gh, _, _) in grains.items()}
    a, b = v["pymtex"], v["matlab"]
    np.testing.assert_array_equal(np.isnan(a), np.isnan(b))
    m = ~np.isnan(a)
    if mp.kind == "gnd":
        assert np.median(np.abs(a[m] - b[m]) / np.abs(b[m]).clip(1)) < 1e-4
        assert np.nanmean(a) == pytest.approx(np.nanmean(b), rel=1e-4)
    else:
        assert np.abs(a[m] - b[m]).max() < 1e-4


@pytest.mark.parametrize("key", ["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"])
@pytest.mark.parametrize("vec", [(0, 0, 1), (1, 0, 0), (0.3, -0.5, 0.8)], ids=["Z", "X", "custom"])
def test_ipf_colors(both, key, vec):
    for phase in ("Ti (alpha)", "Ti (beta)"):
        c = {n: be.ipf_colors(h, phase, key, vec) for n, (be, h) in both.items()}
        a, b = c["pymtex"], c["matlab"]
        np.testing.assert_array_equal(np.isnan(a[:, 0]), np.isnan(b[:, 0]))
        m = ~np.isnan(a[:, 0])
        if m.any():
            assert np.abs(a[m] - b[m]).max() < 1e-3


def test_both_backends_render(both, tmp_path):
    for n, (be, h) in both.items():
        d = be.get_ebsd(h)
        rgb = be.ipf_colors(h, "Ti (alpha)", "ipfHKLKey", (0, 0, 1))
        seg = {"x1": np.array([0.0]), "y1": np.array([0.0]), "x2": np.array([10.0]), "y2": np.array([10.0])}
        for i, spec in enumerate([RenderSpec(rgb=rgb, seg=seg, seg_rgb=np.array([[0, 0, 0]]), dpi=72),
                                  RenderSpec(values=d.props["ci"], colorbar_label="CI", dpi=72)]):
            f = be.render(h, spec, tmp_path / f"{n}_{i}.png")
            assert f.stat().st_size > 1000
        assert be.render_key(h, "Ti (beta)", "ipfHKLKey", (0, 0, 1), tmp_path / f"{n}_key.png", 72).is_file()
