"""Phase 2 on the pymtex backend: cleanup, grains, boundaries, per-pixel maps (300x300 crops)."""
import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import (BURGERS_TYPES, CLEANUP_PRESETS, CleanupParams, GrainParams, LoadParams,
                                   MapParams, cleanup_params_for)
from ti64app.backends.hexgrid import dilation_fill, neighbour_table
from ti64app.ops_ebsd import BoundaryParams, classify_boundaries


# ---- grid helpers (shared algorithm with matlab/+ti64) ------------------------------------------
def _hex(nr=4, nc=5, dx=0.2):
    x, y = [], []
    for r in range(nr):
        for c in range(nc if r % 2 == 0 else nc - 1):
            x.append(c * dx + (dx / 2 if r % 2 else 0))
            y.append(r * dx * np.sqrt(3) / 2)
    return np.array(x), np.array(y)


def test_neighbour_table_hex():
    x, y = _hex()
    nb = neighbour_table(x, y, np.ones(x.size, bool))
    i = 5 + 1                                    # row 1 (odd, 4 px), second pixel at x = 0.3
    assert x[i] == pytest.approx(0.3)
    got = {(round(x[j], 2), round(y[j], 3)) for j in nb[i] if j >= 0}
    want = {(0.1, 0.173), (0.5, 0.173), (0.2, 0.0), (0.4, 0.0), (0.2, 0.346), (0.4, 0.346)}
    assert got == want
    assert (nb[0] >= 0).sum() == 2               # corner pixel (0, 0): only E and SE exist


def test_dilation_fill_majority_and_ties():
    x, y = _hex()
    nb = neighbour_table(x, y, np.ones(x.size, bool))
    gid = np.where(x < 0.45, 1, 2).astype(np.int64)
    to_fill = np.zeros(x.size, bool)
    i = 6
    to_fill[i] = True
    ok = ~to_fill
    src, sweeps = dilation_fill(to_fill, gid.copy(), ok, nb)
    assert sweeps == 1 and gid[src[i]] == 1      # 4 of 6 neighbours belong to grain 1
    # unreachable pixel (no valid neighbour) stays unfilled
    src2, _ = dilation_fill(np.ones(x.size, bool), gid.copy(), np.zeros(x.size, bool), nb)
    assert (src2 == -1).all()


# ---- cleanup ----------------------------------------------------------------------------------
def test_presets_follow_the_guide():
    assert CLEANUP_PRESETS["raw"].ci_threshold == 0.1
    assert CLEANUP_PRESETS["oim_cleaned"].ci_threshold is None
    for p in CLEANUP_PRESETS.values():
        assert (p.noise_angle_deg, p.noise_max_indexed_px, p.noise_max_void_px) == (3.0, 2, 4)
        assert p.fill and not p.spike_removal and not p.hq_filter
    assert cleanup_params_for("raw", {"spike_removal": True}).spike_removal


@pytest.fixture(scope="module")
def ju02_loaded():
    from tests.conftest import CROPS
    f = sorted(CROPS.glob("*JU02*_crop300.ang"))
    if not f:
        pytest.skip("crops missing")
    be = get_backend("pymtex")
    return be, be.load_ebsd(LoadParams(file=f[0], input_state="oim_cleaned"))


@pytest.mark.parametrize("state,expect", [
    ("oim_cleaned", dict(n_ci_removed=0, n_noise_indexed_removed=161, n_small_void_px=1, n_filled=162, n_unfilled=0)),
    ("raw", dict(n_ci_removed=2954, n_noise_indexed_removed=211, n_small_void_px=876, n_filled=1066, n_unfilled=21)),
])
def test_cleanup_presets_on_ju02(ju02_loaded, state, expect):
    """Reference counts (identical in MATLAB MTEX 7.2, see conformance tests)."""
    be, h = ju02_loaded
    before = be.get_ebsd(h)
    h2, rep = be.cleanup(h, cleanup_params_for(state))
    for k, v in expect.items():
        assert getattr(rep, k) == v, k
    after = be.get_ebsd(h2)
    np.testing.assert_array_equal(be.get_ebsd(h).phase, before.phase)     # input untouched
    flags = after.props["cleanup"]
    assert (flags == 4).sum() == rep.n_filled and (flags == 5).sum() == rep.n_unfilled
    filled = flags == 4
    assert not np.isnan(after.quat[filled]).any() and (after.phase[filled] > 0).all()
    assert rep.n_indexed_after == int((after.phase > 0).sum())


def test_cleanup_spike_and_filter_run(ju02_loaded):
    be, h = ju02_loaded
    _, rep = be.cleanup(h, CleanupParams(ci_threshold=0.1, spike_removal=True, hq_filter=True))
    assert rep.n_spikes_removed == 87 and rep.n_filtered_changed > 0


# ---- grains, boundaries, maps -----------------------------------------------------------------
@pytest.fixture(scope="module")
def ju02_grains(ju02_loaded):
    be, h = ju02_loaded
    h2, _ = be.cleanup(h, cleanup_params_for("oim_cleaned"))
    gh = be.calc_grains(h2, GrainParams())
    return be, gh, be.get_grains(gh)


def test_grains_and_boundaries(ju02_grains):
    be, gh, gd = ju02_grains
    assert gh.n_grains == 650 and len(gd.grain["id"]) == 650
    assert gd.pixel_grain_id.shape == (90000,)
    assert gd.grain["is_edge"].sum() == 101
    gb = gd.boundary
    assert gb["inner"].sum() == 2005                       # LAGB segments inside grains
    cls = classify_boundaries(gb, GrainParams(), BoundaryParams())
    L = {k: gb["length_um"][m].sum() for k, m in cls.items()}
    assert L["hagb"] == pytest.approx(3824.96, abs=0.1) and L["lagb"] == pytest.approx(230.88, abs=0.1)
    assert L["burgers_2"] == pytest.approx(1844.23, abs=0.1)   # 60 deg <11-20> dominates
    mis = gb["mis_deg"][cls["lagb"]]
    assert (mis >= 2).all() and (mis < 15).all()


def test_burgers_types_from_mtex(ju02_loaded):
    from ti64app.backends.pymtex_analysis import burgers_types
    be, h = ju02_loaded
    e = be._get(h)
    csA, csB = e["Ti (alpha)"].CS, e["Ti (beta)"].CS
    _, ang = burgers_types(csB, csA)
    assert ang == list(BURGERS_TYPES)


@pytest.mark.parametrize("kind,lo,hi", [("kam", 0.3, 0.45), ("grod", 1.5, 2.5), ("gos", 1.5, 2.5),
                                         ("gnd", 1e14, 2e14)])
def test_orientation_maps(ju02_grains, kind, lo, hi):
    be, gh, gd = ju02_grains
    v = be.compute_map(gh, MapParams(kind=kind))
    assert v.shape == (90000,)
    assert lo < np.nanmean(v) < hi
    if kind == "gos":   # constant inside each grain
        g = gd.pixel_grain_id
        ok = ~np.isnan(v)
        for gid in np.unique(g[ok])[:50]:
            assert np.ptp(v[ok & (g == gid)]) < 1e-9
    if kind == "kam":
        assert np.nanmax(v) <= 5.0 + 1e-9                    # cutoff
