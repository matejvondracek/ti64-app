"""Phase 6 conformance: the compare-backends tool runs one step on pymtex and MATLAB and reports the numbers and
both figures. Tolerances as in test_phase3 / decisions D13 (crop1000, ideal Burgers)."""
from __future__ import annotations

import json

import pytest

from ti64app import mcp_server, service
from ti64app.backends import get_backend
from ti64app.project import Project
from tests.conftest import sample_params

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    return be


def _rows(table: dict) -> dict[str, dict]:
    return {r["key"]: r for r in table["rows"]}


def test_compare_cleanup_through_mcp(matlab, crop300_ju02):
    p = Project.create("cmp300")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    n_before = len(p.recipe().steps)
    out = mcp_server.compare_backends("cmp300", "ebsd.cleanup", {"map_id": "JU02_c300"})
    assert out["n_values"] > 10 and out["max_rel_diff"] == 0.0          # cleanup is identical (phase 2)
    assert set(out["figures"]) == {"figure_pymtex", "figure_matlab", "side_by_side"}
    assert out["versions"]["matlab"]["mtex"].endswith("7.2.0")
    steps = p.recipe().steps
    assert len(steps) == n_before + 1 and steps[-1].op == "compare.backends"   # one step, single-backend recipe
    assert not list(p.cache_dir.glob("compare_*"))                              # scratch removed


def test_compare_reconstruction(matlab, crop1000):
    p = Project.create(f"cmp_{crop1000.name[8:12]}")
    service.run(p, "sample.set", sample_params(crop1000, "c1000"))
    service.run(p, "ebsd.load", {"map_id": "c1000"})
    st = service.run(p, "compare.backends", {"op": "recon.run",
                                             "params": {"map_id": "c1000", "params": {"orMode": "burgers"}},
                                             "figure_kind": "parent_ipf"})
    t = json.loads((p.root / st.outputs["table"].path).read_text(encoding="utf-8"))
    r = _rows(t)
    assert r["summary.or_used"]["same"] and r["summary.n_noise_px"]["same"]
    a, b = r["summary.n_grains_all"]["a"], r["summary.n_grains_all"]["b"]
    assert abs(a - b) <= max(3, 0.02 * a)
    assert abs(r["summary.mean_fit_deg"]["abs_diff"]) < 0.01
    assert abs(r["summary.rec_frac"]["abs_diff"]) < 0.002
    assert t["differing_strings"] == []
    assert {"figure_pymtex", "figure_matlab", "side_by_side"} <= set(st.outputs)
    assert st.metrics["pymtex_seconds"] > 0 and st.metrics["matlab_seconds"] > 0
