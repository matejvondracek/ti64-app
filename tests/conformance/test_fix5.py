"""Phase 6.6 fix 5: the single cleanup operations (incl. OIM-style grain dilation) in both backends on JU04_area1
(raw APEX export, hex grid, 247,484 points). Counts compared within tolerances: the grains of the noise / void /
dilation steps come from each engine's calcGrains (alpha = 0), which differ slightly between MTEX 7.2 and pymtex."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams

pytestmark = [pytest.mark.matlab, pytest.mark.slow]
ANG = Path(__file__).resolve().parents[2] / "data" / "JU04" / "JU04_area1.ang"
CHAIN = [("ci_threshold", {"ci_threshold": 0.1}), ("spike_removal", {}), ("noise_removal", {}), ("void_fill", {}),
         ("grain_dilation", {}), ("grain_dilation", {"single_iteration": True, "multi_row": True, "min_grain_px": 4}),
         ("smoothing", {})]


@pytest.fixture(scope="module")
def runs():
    if not ANG.is_file():
        pytest.skip("data/JU04/JU04_area1.ang missing (git-ignored)")
    out = {}
    for name in ("pymtex", "matlab"):
        be = get_backend(name)
        if not be.is_available():
            pytest.skip(f"{name} unavailable: {be.unavailable_reason()}")
        h = be.load_ebsd(LoadParams(file=ANG, input_state="raw", phase_names={1: "Ti (beta)", 2: "Ti (alpha)"}))
        reps, phases = [], []
        cur = h
        for op, prm in CHAIN:
            new, rep = be.clean_step(cur, op, prm)
            reps.append(rep)
            phases.append(be.get_ebsd(new).phase)
            if op != "grain_dilation":                     # both dilation variants start from the void-filled map
                cur = new
        out[name] = (reps, phases)
    return out


def test_same_report_keys_and_exact_ci(runs):
    (a, pa), (b, pb) = runs["pymtex"], runs["matlab"]
    for ra, rb in zip(a, b):
        assert set(ra) - {"summary"} == set(rb) - {"summary"}, (ra["operation"], set(ra) ^ set(rb))
    assert a[0]["n_removed"] == b[0]["n_removed"] and np.array_equal(pa[0], pb[0])


@pytest.mark.parametrize("k", range(1, len(CHAIN)))
def test_counts_within_tolerance(runs, k):
    ra, rb = runs["pymtex"][0][k], runs["matlab"][0][k]
    n = ra["n_points"]
    for key in ("n_removed", "n_filled", "n_unfilled", "n_changed", "n_indexed_after", "n_left_large_void_px"):
        if key in ra:
            assert abs(ra[key] - rb[key]) <= max(25, 0.01 * n), (ra["operation"], key, ra[key], rb[key])
    if ra["operation"] == "grain_dilation":
        ia, ib = ra["changed_per_iteration"], rb["changed_per_iteration"]
        assert abs(len(ia) - len(ib)) <= max(3, 0.1 * len(ia))
        assert abs(ia[0] - ib[0]) <= 0.02 * max(ia[0], 1) + 25
    # same phase per pixel for at least 99 % of the points
    agree = np.mean(runs["pymtex"][1][k] == runs["matlab"][1][k])
    assert agree > 0.99, (ra["operation"], agree)
