"""Phase 3 conformance: prior-beta reconstruction, pymtex vs MATLAB/MTEX 7.2, on the three 1000x1000 crops
(~200 um, whole prior-beta grains) with the SAME OR (ideal Burgers; D12: the refined ORs differ by ~0.04 deg,
so the 1 deg rule can pick different ORs in the two backends).

Tolerances (measured 2026-10-31 on the three crops, docs/decisions.md D13):
  parent map    reconstruction state and phase equal on >= 99.99 % of pixels (JU02, RM02 identical; RI02: 19 px)
  parent orient. >= 99.5 % of reconstructed pixels within 2 deg (cubic symmetry; measured >= 99.76 %)
  grains        all fragments rel. 5 %; grains >= histMinECD within max(2, 10 %)
  fit           mean fit |diff| <= 0.05 deg; reconstructed fraction |diff| <= 0.002
  ECD           area-weighted rel. 5 %
  boundaries    pre-merge beta/beta boundary length rel. 5 %; neighbour misorientation shares |diff| <= 0.01
  variants      Python numbering (canonical grain mean): same variant ID on >= 99.9 % of reconstructed
                pixels (measured >= 99.98 %)
"""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from ti64app.backends import get_backend
from ti64app.backends.base import RECON_TRANSFORMED, LoadParams, ReconParams
from ti64app.modules.recon_analysis import grain_mean_parent, ti_variants

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


def _run(be, f, rp):
    h = be.load_ebsd(LoadParams(file=f, input_state="oim_cleaned"))
    rh, res = be.reconstruct(h, rp)
    child, par, rd = be.get_ebsd(h), be.get_ebsd(rh), be.get_recon(rh)
    st = par.props["recon_state"].astype(int)
    v = ti_variants(np.where((st == RECON_TRANSFORMED)[:, None], child.quat, np.nan),
                    grain_mean_parent(par.props["parent_grain"], rd.grain))
    be.release(rh)
    be.release(h)
    return {"res": res, "par": par, "rd": rd, "st": st, "var": v}


@pytest.fixture
def recon(matlab, crop1000):
    rp = ReconParams(orMode="burgers")
    return {"pymtex": _run(get_backend("pymtex"), crop1000, rp), "matlab": _run(matlab, crop1000, rp)}


def cubic_misorientation_deg(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """Disorientation angle of two cubic orientations (crystal -> specimen quaternions a b c d)."""
    sym = Rotation.create_group("O")
    r1 = Rotation.from_quat(np.c_[q1[:, 1:], q1[:, 0]])
    r2 = Rotation.from_quat(np.c_[q2[:, 1:], q2[:, 0]])
    d = r1.inv() * r2                                   # misorientation in crystal 1 frame
    best = np.full(len(q1), 180.0)
    for s in sym:
        best = np.minimum(best, np.degrees((d * s).magnitude()))
    return best


def test_parent_map(recon):
    a, b = recon["pymtex"], recon["matlab"]
    assert np.mean(a["st"] == b["st"]) >= 0.9999
    assert np.mean(a["par"].phase == b["par"].phase) >= 0.9999
    m = (a["st"] == RECON_TRANSFORMED) & (b["st"] == RECON_TRANSFORMED)
    ang = cubic_misorientation_deg(a["par"].quat[m], b["par"].quat[m])
    assert np.mean(ang < 2.0) >= 0.995


def test_summary_numbers(recon):
    a, b = recon["pymtex"]["res"], recon["matlab"]["res"]
    assert a["or_used"] == b["or_used"] == "burgers"
    assert a["n_grains_all"] == pytest.approx(b["n_grains_all"], rel=0.05)
    assert abs(a["n_grains_min_ecd"] - b["n_grains_min_ecd"]) <= max(2, 0.1 * b["n_grains_min_ecd"])
    assert abs(a["mean_fit_deg"] - b["mean_fit_deg"]) <= 0.05
    assert abs(a["rec_frac"] - b["rec_frac"]) <= 0.002
    assert a["ecd_aw_um"] == pytest.approx(b["ecd_aw_um"], rel=0.05)
    assert np.abs(np.subtract(a["mis_share"], b["mis_share"])).max() <= 0.01
    assert len(a["sweep"]) == len(b["sweep"]) == 5


def test_boundaries_and_variants(recon):
    a, b = recon["pymtex"], recon["matlab"]
    la, lb = a["rd"].pre_boundary["length_um"].sum(), b["rd"].pre_boundary["length_um"].sum()
    assert la == pytest.approx(lb, rel=0.05)
    for r in (a, b):
        g = r["rd"].grain
        assert g["mean_quat"].shape == (len(g["id"]), 4) and set(r["rd"].boundary) >= {"grain1", "grain2", "mis_deg"}
    m = (a["st"] == RECON_TRANSFORMED) & (b["st"] == RECON_TRANSFORMED)
    assert np.mean(a["var"]["variant"][m] == b["var"]["variant"][m]) >= 0.999
    assert np.mean(a["var"]["packet"][m] == b["var"]["packet"][m]) >= 0.999
