"""Fix 3 conformance, pymtex vs MATLAB/MTEX 7.2:
- prior-beta grains (grains.calc on the reconstructed parent map, fix 3.10) on the JU02 1000x1000 crop with the same
  OR (ideal Burgers): grain counts rel. 10 %, inner-grain area-weighted ECD rel. 10 %, HAGB length rel. 10 %
  (the parent maps agree on >= 99.99 % of the pixels, test_phase3; segmentation adds the D7 void differences);
- "Save" of a figure (fix 3.14): MATLAB writes .fig, .svg and .pdf; pymtex .svg and .pdf (numbers are compared,
  not images: only that each file is a valid file of its type)."""
from __future__ import annotations

import pytest

from ti64app import service
from ti64app.backends import get_backend
from tests.conftest import sample_params

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be


def _chain(tmp_path, be: str, crop):
    from ti64app.project import Project

    p = Project.create(f"fix3_{be}", base=tmp_path, backend=be)
    service.run(p, "sample.set", sample_params(crop, "JU02_c1000"))
    service.run(p, "ebsd.load", {"map_id": "JU02_c1000"})
    r = service.run(p, "recon.run", {"map_id": "JU02_c1000", "params": {"orMode": "burgers"}})
    g = service.run(p, "grains.calc", {"map_id": "JU02_c1000", "source": r.id})
    return p, g


def test_prior_beta_grains_both_backends(matlab, tmp_path):
    import json

    from tests.conftest import CROPS, _one

    crop = _one(CROPS, "*JU02*_crop1000.ang")
    out = {}
    for be in ("pymtex", "matlab"):
        p, g = _chain(tmp_path, be, crop)
        out[be] = json.loads((p.root / g.outputs["summary"].path).read_text(encoding="utf-8"))
    a, b = out["pymtex"], out["matlab"]
    assert abs(a["n_grains"] - b["n_grains"]) <= max(3, 0.1 * a["n_grains"])
    assert a["ecd_um_area_weighted_inner"] == pytest.approx(b["ecd_um_area_weighted_inner"], rel=0.1)
    assert a["boundary_length_um"]["hagb"] == pytest.approx(b["boundary_length_um"]["hagb"], rel=0.1)


def test_save_figure_formats(matlab, tmp_path, crop300_ju02):
    from ti64app.project import Project

    for be, fmts in service.EDITABLE_FORMATS.items():
        p = Project.create(f"save_{be}", base=tmp_path, backend=be)
        service.run(p, "sample.set", sample_params(crop300_ju02))
        service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
        st = service.run(p, "figure", {"map_id": "JU02_c300", "kind": "phase"})
        for fmt in fmts:
            f = service.save_figure(p, st.id, fmt, tmp_path / be / f"phase.{fmt}")
            head = f.read_bytes()[:16]
            assert f.stat().st_size > 1000
            if fmt == "pdf":
                assert head.startswith(b"%PDF")
            elif fmt == "svg":
                assert b"<svg" in f.read_bytes()[:4000]
            else:                                           # .fig = MAT-file
                assert head.startswith(b"MATLAB")
