"""Phase 4 conformance: grain size, pymtex vs MATLAB/MTEX 7.2, through the service ops (same chain in both).

crop300 (3 maps), lath partition at 5° (so that the colony merge has 10.53° pairs to merge):
  per grain (matched by identical pixel partition of indexed pixels)  perimeter, longest caliper rel. 1e-3 on >= 99 %
              (RI02: not-indexed pixels next to voids join different grains, D7); shortest caliper rel. 1e-3 on >= 93 % (measured 95.5-97.8 %; the engines' minimum-width
              algorithms differ on small grains, up to 12 %), mean rel. diff < 1 %
  colonies    identical partition of indexed pixels, same number of merged laths
  summary     counts |diff| <= 2; ECD / thickness / minor-axis means rel. 1e-3
crop1000 JU02 with reconstruction (ideal Burgers in both, D12), lath partition 15° (default):
  prior beta  E112 width / length rel. 2 %, area-weighted ECD and calipers rel. 5 % (parent grains differ
              slightly between the engines, D13); alpha statistics rel. 1e-3
Tolerances measured 2026-10-31 (docs/decisions.md D14).
"""
import json

import numpy as np
import pytest

from tests.conftest import sample_params
from ti64app import service
from ti64app.backends import get_backend

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


def _chain(tmp_path, f, be, hagb, recon=False):
    from ti64app.project import Project

    p = Project.create(f"gs_{be}", base=tmp_path)
    mid = "m"
    service.run(p, "sample.set", sample_params(f, mid, 270.0))
    service.run(p, "ebsd.load", {"map_id": mid}, backend=be)
    if recon:
        service.run(p, "recon.run", {"map_id": mid, "params": {"orMode": "burgers"}}, backend=be)
    service.run(p, "ebsd.cleanup", {"map_id": mid}, backend=be)
    g = service.run(p, "grains.calc", {"map_id": mid, "grains": {"hagb_deg": hagb}}, backend=be)
    s = service.run(p, "grainsize.run", {"map_id": mid}, backend=be)
    out = {"summary": json.loads((p.root / s.outputs["summary"].path).read_text(encoding="utf-8"))}
    import pandas as pd

    out["alpha"] = pd.read_csv(p.root / s.outputs["alpha_grains"].path)
    out["colonies"] = pd.read_csv(p.root / s.outputs["colonies"].path)
    with np.load(p.root / g.outputs["grains"].path) as z:
        out["pix"] = z["pixel_grain_id"]
    with np.load(p.root / s.outputs["pixels"].path) as z:
        out["cpix"] = z["colony_id"]
    with np.load(p.root / service.latest_step(p, ("ebsd.cleanup",)).outputs["ebsd"].path) as z:
        out["phase"] = z["phase"]
    return out


def _match(pa, pb, idx):
    """Grain id pairs (a, b) of identical partitions on the pixels idx; None if not a bijection."""
    pairs = np.unique(np.c_[pa[idx], pb[idx]], axis=0)
    if len(np.unique(pairs[:, 0])) != len(pairs) or len(np.unique(pairs[:, 1])) != len(pairs):
        return None
    return pairs


def test_laths_colonies_crop300(tmp_path, matlab, crop300):
    a = _chain(tmp_path / "a", crop300, "pymtex", 5.0)
    b = _chain(tmp_path / "b", crop300, "matlab", 5.0)
    idx = a["phase"] > 0
    pairs = _match(a["pix"], b["pix"], idx)
    assert pairs is not None
    ta, tb = a["alpha"].set_index("id"), b["alpha"].set_index("id")
    A, B = ta.loc[pairs[:, 0]], tb.loc[pairs[:, 1]]
    np.testing.assert_array_equal(A["is_edge"].to_numpy(), B["is_edge"].to_numpy())
    for c, share in (("perimeter_um", 0.99), ("caliper_long_um", 0.99), ("caliper_short_um", 0.93)):
        rel = np.abs(A[c].to_numpy() - B[c].to_numpy()) / np.maximum(B[c].to_numpy(), 1e-9)
        # shortest caliper: the engines' minimum-width algorithms differ on ~2 % of (small) grains (D14)
        assert np.median(rel) < 1e-4 and np.mean(rel < 1e-3) >= share and rel.mean() < 0.01, c
    el = (A["caliper_long_um"] / A["caliper_short_um"]).to_numpy() >= 1.5
    d = np.abs(A["caliper_long_angle_deg"].to_numpy() - B["caliper_long_angle_deg"].to_numpy()) % 180
    d = np.minimum(d, 180 - d)[el]
    assert np.mean(d < 0.1) >= 0.98            # rest: ties between equally long diagonals
    # ellipse + intercepts are computed in Python from the partition -> equal except where a not-indexed pixel
    # next to a void joined a different grain (D7; RI02: 2 of 1767 grains)
    for c in ("ellipse_minor_um", "thickness_intercept_um"):
        same = np.isclose(A[c].to_numpy(), B[c].to_numpy(), rtol=1e-6, equal_nan=True)
        assert same.mean() >= 0.995, c
    cp = _match(a["cpix"], b["cpix"], idx)
    assert cp is not None, "colony partitions differ"
    sa, sb = a["summary"], b["summary"]
    assert sa["colony"]["n_alpha_grains_merged"] == sb["colony"]["n_alpha_grains_merged"] > 0
    for key in ("ecd", "thickness_intercept", "thickness_minor_axis"):
        assert sa["alpha"][key]["mean"] == pytest.approx(sb["alpha"][key]["mean"], rel=1e-3), key
    assert abs(sa["alpha"]["n_used"] - sb["alpha"]["n_used"]) <= 2
    assert sa["colony"]["ecd"]["area_weighted"]["mean"] == pytest.approx(sb["colony"]["ecd"]["area_weighted"]["mean"],
                                                                         rel=1e-3)
    assert sa["retained_beta"]["loaded"] == sb["retained_beta"]["loaded"]
    ra, rb = sa["retained_beta"]["source_map"], sb["retained_beta"]["source_map"]
    assert (ra["beta_px"], ra["indexed_px"]) == (rb["beta_px"], rb["indexed_px"])


@pytest.mark.slow
def test_prior_beta_crop1000_ju02(tmp_path, matlab):
    from tests.conftest import CROPS

    f = sorted(CROPS.glob("*JU02*_crop1000.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    sa = _chain(tmp_path / "a", f[0], "pymtex", 15.0, recon=True)["summary"]
    sb = _chain(tmp_path / "b", f[0], "matlab", 15.0, recon=True)["summary"]
    for key in ("ecd", "thickness_intercept", "thickness_minor_axis"):
        assert sa["alpha"][key]["mean"] == pytest.approx(sb["alpha"][key]["mean"], rel=1e-3)
    pa, pb = sa["prior_beta"], sb["prior_beta"]
    for k in ("across_bd", "along_bd"):
        assert pa[k]["mean_e112_um"] == pytest.approx(pb[k]["mean_e112_um"], rel=0.02)
    assert pa["ecd"]["area_weighted"]["mean"] == pytest.approx(pb["ecd"]["area_weighted"]["mean"], rel=0.05)
    for k in ("across_bd_um", "along_bd_um"):
        assert pa["caliper"][k]["area_weighted_mean"] == pytest.approx(pb["caliper"][k]["area_weighted_mean"], rel=0.05)
    assert sa["retained_beta"]["loaded"] == sb["retained_beta"]["loaded"]
