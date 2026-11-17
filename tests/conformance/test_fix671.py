"""Phase 6.7 fix 1, item 5: grain CI standardization in both backends (JU02 300x300 crop and the raw APEX export
JU04_area1, hex grid). The grains come from each engine's calcGrains (alpha = 0), which differ slightly between MTEX 7.2
and pymtex, so counts are compared within tolerances and the CI per point for >= 99 % of the points.
Item 1: the reconstruction of an alpha-only map (JU04_area3 head crop) adds the parent phase in both backends."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams

pytestmark = [pytest.mark.matlab]
REPO = Path(__file__).resolve().parents[2]


def _backends():
    out = {}
    for name in ("pymtex", "matlab"):
        be = get_backend(name)
        if not be.is_available():
            pytest.skip(f"{name} unavailable: {be.unavailable_reason()}")
        out[name] = be
    return out


def _grain_ci_runs(f: Path, state: str, names: dict):
    out = {}
    for name, be in _backends().items():
        h = be.load_ebsd(LoadParams(file=f, input_state=state, phase_names=names))
        d, _ = be.clean_step(h, "grain_dilation", {})
        res = {}
        for mode in ("mean", "max"):
            n, rep = be.clean_step(d, "grain_ci", {"mode": mode})
            res[mode] = (rep, be.get_ebsd(n).props["ci"].astype(float), be.get_ebsd(d).props["ci"].astype(float))
        out[name] = res
    return out


def _check(runs):
    for mode in ("mean", "max"):
        (ra, ca, c0a), (rb, cb, c0b) = runs["pymtex"][mode], runs["matlab"][mode]
        assert set(ra) == set(rb), set(ra) ^ set(rb)
        assert np.allclose(c0a, c0b)                                      # same input CI (after dilation)
        n = ra["n_points"]
        for k in ("n_in_grains", "n_changed"):
            assert abs(ra[k] - rb[k]) <= max(25, 0.01 * n), (mode, k, ra[k], rb[k])
        assert abs(ra["n_grains"] - rb["n_grains"]) <= max(5, 0.02 * ra["n_grains"]), (mode, ra["n_grains"],
                                                                                       rb["n_grains"])
        assert np.mean(np.isclose(ca, cb, atol=1e-4)) > 0.99, mode
        assert abs(ra["ci_mean_after"] - rb["ci_mean_after"]) < 0.01


def test_grain_ci_conformance_crop(crop300_ju02):
    _check(_grain_ci_runs(crop300_ju02, "oim_cleaned", {1: "Ti (beta)", 2: "Ti (alpha)"}))


@pytest.mark.slow
def test_grain_ci_conformance_raw_apex():
    f = next((REPO / "data" / "JU04").rglob("JU04_area1.ang"), None)
    if f is None:
        pytest.skip("data/JU04/EBSD/JU04_area1.ang missing (git-ignored)")
    _check(_grain_ci_runs(f, "raw", {1: "Ti (beta)", 2: "Ti (alpha)"}))


@pytest.mark.slow
def test_alpha_only_reconstruction_conformance(tmp_path):
    """JU04_area3 (raw APEX export, alpha only): both backends add the parent phase and agree on the result."""
    from tests.integration.test_fix5_orientation import _head_crop
    from ti64app.backends.base import ReconParams

    f = next((REPO / "data" / "JU04").rglob("JU04_area3.ang"), None)
    if f is None:
        pytest.skip("data/JU04/EBSD/JU04_area3.ang missing (git-ignored)")
    crop = _head_crop(f, tmp_path / "a3.ang", 150)
    res = {}
    for name, be in _backends().items():
        h = be.load_ebsd(LoadParams(file=crop, input_state="raw", phase_names={1: "Ti (alpha)"}))
        rh, r = be.reconstruct(h, ReconParams(sweep=[]))
        res[name] = (r, be.get_ebsd(rh).props["recon_state"])
    (a, sa), (b, sb) = res["pymtex"], res["matlab"]
    assert a["beta_added"] is True and b["beta_added"] is True
    assert abs(a["rec_frac"] - b["rec_frac"]) < 0.02
    assert abs(a["n_grains_all"] - b["n_grains_all"]) <= 0.1 * a["n_grains_all"]
    assert np.mean(sa == sb) > 0.97 and not (sa == 4).any() and not (sb == 4).any()
