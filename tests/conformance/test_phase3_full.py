"""Phase 3 on the FULL JU02 map (slow; MATLAB part needs MATLAB): JU02 reference numbers of the Research
Assignment (docs/beta_reconstruction_method.md, MTEX 6.1: 47 grains >= 5 um, 701 fragments, ECDaw 93.9 um,
mean fit 3.38 deg, 50.7 % fit > 3 deg) and the measured results of docs/decisions.md D12.
Each backend reconstructs with its default 1 deg OR rule AND with ideal Burgers (conformance: same OR)."""
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams, ReconParams

pytestmark = pytest.mark.slow


def _full():
    from tests.conftest import DATA

    f = sorted(DATA.glob("*JU02*.ang"))
    if not f:
        pytest.skip("full JU02 map missing")
    return f[0]


def _recon(name, mode):
    be = get_backend(name)
    if not be.is_available():
        pytest.skip(be.unavailable_reason())
    h = be.load_ebsd(LoadParams(file=_full(), input_state="oim_cleaned"))
    rh, res = be.reconstruct(h, ReconParams(orMode=mode))
    be.release(rh)
    be.release(h)
    return res


@pytest.fixture(scope="module")
def results():
    out = {}
    yield out
    if get_backend("matlab").running:
        get_backend("matlab").close()


@pytest.mark.parametrize("mode", ["limit", "burgers"])
def test_pymtex_full(results, mode):
    r = results[("pymtex", mode)] = _recon("pymtex", mode)
    # pymtex refines to ~1.01 deg -> the 1 deg rule falls back to Burgers (D12)
    assert r["or_used"] == "burgers" and r["or_dev_deg"] == pytest.approx(1.01, abs=0.05)
    assert r["n_grains_min_ecd"] == pytest.approx(53, abs=3) and r["n_grains_all"] == pytest.approx(796, rel=0.03)
    assert r["mean_fit_deg"] == pytest.approx(3.566, abs=0.03) and r["rec_frac"] > 0.998


@pytest.mark.matlab
@pytest.mark.parametrize("mode", ["limit", "burgers"])
def test_matlab_full(results, mode):
    r = results[("matlab", mode)] = _recon("matlab", mode)
    if mode == "limit":       # refined OR 0.97 deg kept: close to the MTEX 6.1 reference (0.82 deg, 47 / 701)
        assert r["or_used"] == "refined" and r["or_dev_deg"] == pytest.approx(0.97, abs=0.05)
        assert r["n_grains_min_ecd"] == pytest.approx(47, abs=3) and r["n_grains_all"] == pytest.approx(701, rel=0.03)
        assert r["mean_fit_deg"] == pytest.approx(3.38, abs=0.05) and r["ecd_aw_um"] == pytest.approx(93.9, rel=0.03)
        assert r["bad_fit_frac"] == pytest.approx(0.507, abs=0.01)
    else:
        assert r["n_grains_min_ecd"] == pytest.approx(52, abs=3) and r["mean_fit_deg"] == pytest.approx(3.568, abs=0.03)


@pytest.mark.matlab
def test_same_or_conformance_full(results):
    if ("pymtex", "burgers") not in results or ("matlab", "burgers") not in results:
        pytest.skip("needs test_pymtex_full[burgers] and test_matlab_full[burgers] in the same run")
    a, b = results[("pymtex", "burgers")], results[("matlab", "burgers")]
    assert abs(a["n_grains_min_ecd"] - b["n_grains_min_ecd"]) <= 3
    assert a["n_grains_all"] == pytest.approx(b["n_grains_all"], rel=0.03)
    assert abs(a["mean_fit_deg"] - b["mean_fit_deg"]) <= 0.02
    assert a["ecd_aw_um"] == pytest.approx(b["ecd_aw_um"], rel=0.03)
