"""Phase 2 service operations (pymtex): recipe chain, figures, replay, MCP tools, OIM comparison."""
import json

import numpy as np
import pytest
from PIL import Image

from ti64app import mcp_server, service
from ti64app.ops_ebsd import DataUnavailable
from ti64app.io.ang import PriasUnavailable
from tests.conftest import sample_params

FIGS = [{"kind": "ipf", "overlays": ["hagb"]}, {"kind": "ipf", "direction": "build"},
        {"kind": "ipf", "phases": ["Ti (alpha)"]}, {"kind": "phase"}, {"kind": "ci"}, {"kind": "iq"},
        {"kind": "kam", "overlays": ["hagb"]}, {"kind": "grod"}, {"kind": "gos"}, {"kind": "gnd"},
        {"kind": "boundaries"}, {"kind": "burgers"}, {"kind": "ipf_key", "phases": ["Ti (beta)"]}]


@pytest.fixture(scope="module")
def chain(tmp_path_factory, crop300_ju02_mod):
    from ti64app.project import Project

    p = Project.create("chain", base=tmp_path_factory.mktemp("proj"))
    service.run(p, "sample.set", sample_params(crop300_ju02_mod))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    service.run(p, "ebsd.cleanup", {"map_id": "JU02_c300"})
    service.run(p, "grains.calc", {"map_id": "JU02_c300"})
    for k in ("kam", "grod", "gos", "gnd"):
        service.run(p, "map.compute", {"map_id": "JU02_c300", "kind": k})
    for fp in FIGS:
        service.run(p, "figure", {"map_id": "JU02_c300", **fp})
    return p


@pytest.fixture(scope="module")
def crop300_ju02_mod():
    from tests.conftest import CROPS
    f = sorted(CROPS.glob("*JU02*_crop300.ang"))
    if not f:
        pytest.skip("crops missing")
    return f[0]


def test_chain_records_sources(chain):
    steps = chain.recipe().steps
    by_op = {}
    for s in steps:
        by_op.setdefault(s.op, []).append(s)
    load, clean, grains = by_op["ebsd.load"][0], by_op["ebsd.cleanup"][0], by_op["grains.calc"][0]
    assert clean.params["source"] == load.id and clean.params["preset"] == "oim_cleaned"
    assert clean.params["resolved"]["ci_threshold"] is None
    assert grains.params["source"] == clean.id and f"{clean.id}:ebsd" in grains.inputs
    assert all(s.params["grains"] == grains.id for s in by_op["map.compute"])
    summary = json.loads((chain.root / grains.outputs["summary"].path).read_text())
    assert summary["n_grains"] == 650 and summary["burgers_types_deg"] == [10.53, 60.0, 60.83, 63.26, 90.0]


def test_figures_written(chain):
    figs = [s for s in chain.recipe().steps if s.op == "figure"]
    assert len(figs) == len(FIGS)
    for s in figs:
        im = Image.open(chain.root / s.outputs["figure"].path)
        assert im.width > 200 and im.height > 100
    ci = next(s for s in figs if s.params["kind"] == "ci")
    meta = json.loads((chain.root / ci.outputs["meta"].path).read_text())
    assert meta["warnings"] and "OIM" in meta["warnings"][0]          # OIM-cleaned CI warning
    ipf_b = next(s for s in figs if s.params.get("direction") == "build")
    meta = json.loads((chain.root / ipf_b.outputs["meta"].path).read_text())
    assert meta["keys"]["Ti (alpha)"]["vector"] == [0.0, -1.0, 0.0]   # sample sheet BD = 270 deg


def test_missing_data_is_reported(chain):
    with pytest.raises(DataUnavailable, match="Fit is constant"):
        service.run(chain, "figure", {"map_id": "JU02_c300", "kind": "fit"})
    with pytest.raises(PriasUnavailable, match="no PRIAS data"):
        service.run(chain, "figure", {"map_id": "JU02_c300", "kind": "prias"})


def test_rebuild_after_restart(chain):
    """Backend objects gone (new process): the next op rebuilds load -> cleanup -> grains."""
    service.SESSION.clear()
    s = service.run(chain, "map.compute", {"map_id": "JU02_c300", "kind": "kam", "kam_order": 2})
    v = np.load(chain.root / s.outputs["values"].path)["values"]
    assert np.nanmean(v) > 0


def test_replay_reproduces_everything(chain):
    rep = service.replay(chain)
    assert rep.ok, [c for c in rep.checks if c.status != "identical"]
    assert {c.status for c in rep.checks} == {"identical"}           # incl. PNG pixels
    assert len([c for c in rep.checks if c.output == "figure"]) == len(FIGS)


def test_mcp_phase2_tools(tmp_path, monkeypatch, crop300_ju02_mod):
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(tmp_path))
    mcp_server.set_sample_sheet(project="m", map_file=str(crop300_ju02_mod), specimen_id="EBMX-1",
                                input_state="raw", build_direction_angle_deg=270, map_id="J")
    mcp_server.load_map(project="m", map_id="J")
    c = mcp_server.cleanup_map(project="m", map_id="J", overrides={"spike_removal": True})
    assert c["report"]["n_ci_removed"] == 2954 and c["report"]["n_spikes_removed"] == 87
    g = mcp_server.calc_grains(project="m", map_id="J")
    assert g["summary"]["n_grains"] > 0
    k = mcp_server.compute_map(project="m", map_id="J", kind="kam", kam_order=2, kam_threshold_deg=3)
    assert k["stats"]["unit"] == "deg"
    f = mcp_server.make_figure(project="m", map_id="J", kind="burgers")
    assert f["png"].endswith(".png") and "boundary_length_um" in f["meta"]


def test_compare_oim_detects_flip(chain, tmp_path):
    from ti64app.modules.oim_compare import hex_raster
    from ti64app.backends import get_backend

    load = next(s for s in chain.recipe().steps if s.op == "ebsd.load")
    z = np.load(chain.root / load.outputs["ebsd"].path)
    h = service.materialize(chain, load.id, "pymtex")
    rgb = get_backend("pymtex").ipf_colors(h, "Ti (alpha)", "ipfTSLKey", (0, 0, 1))
    r = (np.clip(np.nan_to_num(hex_raster(z["x"], z["y"], rgb), nan=0.0), 0, 1) * 255).astype(np.uint8)
    canvas = np.full((r.shape[0] + 80, r.shape[1] + 60, 3), 255, np.uint8)
    canvas[30:30 + r.shape[0], 30:30 + r.shape[1]] = r
    for name, arr, expect in (("same", canvas, "identity"), ("flip", canvas[::-1], "flip_y")):
        f = tmp_path / f"{name}.png"
        Image.fromarray(np.ascontiguousarray(arr)).save(f)
        s = service.run(chain, "compare.oim", {"map_id": "JU02_c300", "image": str(f)})
        res = json.loads((chain.root / s.outputs["result"].path).read_text())
        assert res["best"]["transform"] == expect and res["frame_ok"] == (expect == "identity")
