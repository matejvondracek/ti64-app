"""Phase 6.7 conformance: texture and Young's modulus from texture computed by both backends on JU02 agree within
tolerance (numbers, not images). Same inputs: the orientation set is built in Python from one project's recipe (the
same grains), only the ODF / tensor work differs between pymtex and MATLAB."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tests.conftest import DATA, sample_params
from ti64app import service

pytestmark = pytest.mark.matlab
MID = "JU02_c300"


def _chain(tmp_path, ang, map_id, backend):
    from ti64app.project import Project

    p = Project.create(f"p67_{backend}", base=tmp_path, backend=backend)
    service.run(p, "sample.set", {**sample_params(ang, map_id), "position": {"map_x_axis": "+X", "map_y_axis": "-Y"}})
    service.run(p, "ebsd.load", {"map_id": map_id}, backend="pymtex")      # same data and grains for both backends
    service.run(p, "grains.calc", {"map_id": map_id}, backend="pymtex")
    return p


def _summ(p, s):
    return json.loads((p.root / s.outputs["summary"].path).read_text(encoding="utf-8"))


def _compare(p, map_id, weighting="pixel"):
    out = {}
    for be in ("pymtex", "matlab"):
        t = service.run(p, "texture.run", {"map_id": map_id, "weighting": weighting}, backend=be)
        e = service.run(p, "elastic.run", {"map_id": map_id, "bootstrap_runs": 50}, backend=be)
        with np.load(p.root / t.outputs["values"].path) as z:
            vals = {k: z[k] for k in z.files}
        out[be] = (_summ(p, t), vals, _summ(p, e))
    (ta, va, ea), (tb, vb, eb) = out["pymtex"], out["matlab"]
    assert ta["n_grains"] == tb["n_grains"] and ta["n_orientations"] == tb["n_orientations"]
    assert tb["max_mrd"] == pytest.approx(ta["max_mrd"], rel=0.01)
    assert tb["texture_index"] == pytest.approx(ta["texture_index"], rel=0.01)
    for k in va:
        if k.startswith(("pf_", "ipf_")) and k not in ("pf_dirs", "ipf_dirs"):
            assert np.allclose(va[k], vb[k], atol=0.02 * max(1.0, float(np.max(va[k])))), k
    for m in ("voigt", "reuss", "hill"):
        assert np.allclose(ea["matrices_GPa"][m], eb["matrices_GPa"][m], atol=0.05), m
    for ra, rb in zip(ea["E_table"], eb["E_table"]):
        assert rb["E_hill"] == pytest.approx(ra["E_hill"], abs=0.05)
        assert rb["sd_hill"] == pytest.approx(ra["sd_hill"], abs=0.05)     # same grains -> same bootstrap
    return out


@pytest.fixture(scope="module")
def matlab():
    from ti64app.backends import get_backend

    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    return be


@pytest.mark.parametrize("weighting", ["pixel", "grain"])
def test_backends_agree_on_crop(matlab, tmp_path, crop300_ju02, weighting):
    _compare(_chain(tmp_path, crop300_ju02, MID, "pymtex"), MID, weighting)


@pytest.mark.slow
def test_backends_agree_on_full_ju02(matlab, tmp_path):
    full = sorted(DATA.glob("*JU02*.ang"))
    if not full:
        pytest.skip("full JU02 map missing")
    _compare(_chain(tmp_path, full[0], "JU02", "pymtex"), "JU02", "grain")
