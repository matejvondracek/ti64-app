"""Phase 2 conformance on the FULL JU02 map (slow, needs MATLAB): cleanup, grains, boundaries, KAM.

Full-map tolerances (measured 2026-10-31, docs/performance.md): cleanup identical; grain count rel. 1e-3;
boundary length per class rel. 5e-3 (alpha/beta interface: 5e-2, only ~290 um long); KAM per pixel
<= 1e-4 deg. Differences come from not-indexed pixels next to voids (decisions D7).
GND/GROD/GOS on the full map: see scripts/run_phase2_full.py (too slow for the test suite in MATLAB).
"""
import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import GrainParams, LoadParams, MapParams, cleanup_params_for
from ti64app.ops_ebsd import BoundaryParams, classify_boundaries

pytestmark = [pytest.mark.slow, pytest.mark.matlab]


@pytest.fixture(scope="module")
def results():
    from tests.conftest import DATA

    f = sorted(DATA.glob("*JU02*.ang"))
    if not f:
        pytest.skip("full JU02 map missing")
    ml = get_backend("matlab")
    if not ml.is_available():
        pytest.skip(ml.unavailable_reason())
    out = {}
    for n in ("pymtex", "matlab"):
        be = get_backend(n)
        h = be.load_ebsd(LoadParams(file=f[0], input_state="oim_cleaned"))
        h2, rep = be.cleanup(h, cleanup_params_for("oim_cleaned"))
        gh = be.calc_grains(h2, GrainParams())
        out[n] = {"rep": rep, "ebsd": be.get_ebsd(h2), "grains": be.get_grains(gh),
                  "kam": be.compute_map(gh, MapParams(kind="kam"))}
        for x in (h, h2):
            be.release(x)
    yield out
    ml.close()


def test_cleanup_full(results):
    a, b = results["pymtex"], results["matlab"]
    assert a["rep"].model_dump(exclude={"params"}) == b["rep"].model_dump(exclude={"params"})
    np.testing.assert_array_equal(a["ebsd"].phase, b["ebsd"].phase)


def test_grains_and_boundaries_full(results):
    a, b = results["pymtex"]["grains"], results["matlab"]["grains"]
    na, nb = len(a.grain["id"]), len(b.grain["id"])
    assert abs(na - nb) <= 1e-3 * nb
    assert a.grain["is_edge"].sum() == b.grain["is_edge"].sum()
    ca = classify_boundaries(a.boundary, GrainParams(), BoundaryParams())
    cb = classify_boundaries(b.boundary, GrainParams(), BoundaryParams())
    for k in ca:
        la, lb = a.boundary["length_um"][ca[k]].sum(), b.boundary["length_um"][cb[k]].sum()
        assert la == pytest.approx(lb, rel=5e-2 if k == "interface" else 5e-3), k


def test_kam_full(results):
    x, y = results["pymtex"]["kam"], results["matlab"]["kam"]
    np.testing.assert_array_equal(np.isnan(x), np.isnan(y))
    m = ~np.isnan(x)
    assert np.abs(x[m] - y[m]).max() < 1e-4
