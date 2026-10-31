"""Phase 3 service operations (pymtex) on the JU02 1000x1000 crop: recon.run, recon.figure, recon.sweep,
MCP tools, replay. The crop (~200 um) contains whole prior-beta grains (CLAUDE.md)."""
import json

import numpy as np
import pytest
from PIL import Image

from tests.conftest import CROPS, sample_params
from ti64app import mcp_server, service
from ti64app.backends.base import RECON_TRANSFORMED
from ti64app.ops_recon import RECON_FIGURES

MAP = "JU02_c1000"


@pytest.fixture(scope="module")
def crop1000_ju02():
    f = sorted(CROPS.glob("*JU02*_crop1000.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    return f[0]


@pytest.fixture(scope="module")
def rchain(tmp_path_factory, crop1000_ju02):
    from ti64app.project import Project

    p = Project.create("recon", base=tmp_path_factory.mktemp("proj"))
    service.run(p, "sample.set", sample_params(crop1000_ju02, MAP, 270.0))
    service.run(p, "ebsd.load", {"map_id": MAP})
    service.run(p, "recon.run", {"map_id": MAP, "params": {"orMode": "burgers"}})
    for k in RECON_FIGURES:
        service.run(p, "recon.figure", {"map_id": MAP, "kind": k})
    return p


def _out(p, step, name):
    return p.root / step.outputs[name].path


def test_recon_run_outputs(rchain):
    steps = rchain.recipe().steps
    load = next(s for s in steps if s.op == "ebsd.load")
    run = next(s for s in steps if s.op == "recon.run")
    assert run.params["source"] == load.id and run.params["params"]["orMode"] == "burgers"
    s = json.loads(_out(rchain, run, "summary").read_text())
    # values of this crop with ideal Burgers (pymtex 1.0.0); MATLAB within tolerance in conformance tests
    assert s["or_used"] == "burgers" and s["rec_frac"] > 0.99
    assert 250 <= s["n_grains_all"] <= 360 and 15 <= s["n_grains_min_ecd"] <= 30
    assert 3.0 < s["mean_fit_deg"] < 3.8
    assert len(s["sweep"]) == 5 and len(s["sweep"][0]) == 4
    assert sum(s["state_counts"].values()) == 1_000_000
    v = s["variants"]
    assert v["c_dev_deg_p50_p90"][0] < 3.5 and abs(sum(v["variant_share"]) - 1) < 1e-9
    gs = s["grain_shape"]
    assert gs["build_direction"][1] == pytest.approx(-1.0) and gs["n_used"] > 0
    assert gs["aspect_along_across"]["mean"] > 0
    with np.load(_out(rchain, run, "ebsd")) as z:
        st, pg, fit = z["prop_recon_state"], z["prop_parent_grain"], z["prop_recon_fit"]
        assert (pg[st == RECON_TRANSFORMED] > 0).all() and np.isnan(fit[st != RECON_TRANSFORMED]).all()
        assert (z["phase"][st == RECON_TRANSFORMED] == 2).all()
    with np.load(_out(rchain, run, "variants")) as z:
        assert set(np.unique(z["variant"])) <= set(range(13)) and (z["variant"][st == RECON_TRANSFORMED] > 0).all()
    log = (rchain.root / "reconstruction_log.txt").read_text()
    assert log.count("\n") == 1 and "variantGraph seg 1.5 vgThr 5.0" in log


def test_recon_figures(rchain):
    figs = [s for s in rchain.recipe().steps if s.op == "recon.figure"]
    assert [s.params["kind"] for s in figs] == list(RECON_FIGURES)
    for s in figs:
        im = Image.open(_out(rchain, s, "figure"))
        assert im.size[0] > 200, s.params["kind"]
    # still one log line: the figures reuse the reconstruction kept in the session
    assert (rchain.root / "reconstruction_log.txt").read_text().count("\n") == 1


def test_recon_figure_wrong_backend_is_refused(rchain):
    with pytest.raises(Exception, match="engine that computed it|unavailable"):
        service.run(rchain, "recon.figure", {"map_id": MAP, "kind": "fit"}, backend="matlab")


def test_sweep_and_mcp(rchain, monkeypatch):
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(rchain.root.parent))
    r = mcp_server.recon_sweep(rchain.name, MAP, {"mergeThreshold": [0.5, 3.0]}, base={"orMode": "burgers"})
    runs = r["table"]["runs"]
    assert [x["params"]["mergeThreshold"] for x in runs] == [0.5, 3.0]
    assert runs[0]["n_grains_all"] > runs[1]["n_grains_all"] and len(r["thumbnails"]) == 2
    f = mcp_server.recon_figure(rchain.name, MAP, "packet")
    assert f["png"].endswith(".png") and f["meta"]["backend"] == "pymtex"


def test_replay_reproduces_reconstruction(rchain):
    rep = service.replay(rchain)
    bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]
    assert not bad, bad
