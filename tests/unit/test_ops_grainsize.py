"""Phase 4 service operations (pymtex): grainsize.run / figure / compare_oim, tables, MCP, replay.
JU02 300x300 crop (laths, colonies; no prior beta) and JU02 1000x1000 crop with reconstruction (prior beta)."""
import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from tests.conftest import CROPS, sample_params
from ti64app import mcp_server, service
from ti64app.ops_grainsize import GS_FIGURES

MAP = "JU02_c300"


@pytest.fixture(scope="module")
def gchain(tmp_path_factory):
    from ti64app.project import Project

    f = sorted(CROPS.glob("*JU02*_crop300.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    p = Project.create("gs", base=tmp_path_factory.mktemp("proj"))
    service.run(p, "sample.set", sample_params(f[0], MAP, 270.0))
    service.run(p, "ebsd.load", {"map_id": MAP})
    service.run(p, "ebsd.cleanup", {"map_id": MAP})
    service.run(p, "grains.calc", {"map_id": MAP, "grains": {"hagb_deg": 5.0}})
    service.run(p, "grainsize.run", {"map_id": MAP})
    return p


def _step(p, op):
    return [s for s in p.recipe().steps if s.op == op][-1]


def test_grainsize_outputs(gchain):
    s = _step(gchain, "grainsize.run")
    assert s.params["recon"] is None and s.params["grains"] == _step(gchain, "grains.calc").id
    summ = json.loads((gchain.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    a, c = summ["alpha"], summ["colony"]
    # JU02 crop300, laths at 5 deg (pymtex 1.0.0); MATLAB equal within tolerance (conformance test_phase4)
    assert 550 <= a["n_used"] <= 620 and 1.3 < a["ecd"]["mean"] < 1.8
    assert a["ecd"]["area_weighted"]["mean"] > a["ecd"]["mean"]
    assert 0.3 < a["thickness_intercept"]["median"] < 0.9
    assert a["thickness_minor_axis"]["mean"] > a["thickness_intercept"]["mean"]       # 2b = 1.155 w for a rectangle
    assert c["n_alpha_grains_merged"] > 30 and c["ecd"]["mean"] > a["ecd"]["mean"]
    assert summ["prior_beta"] is None
    assert summ["retained_beta"]["loaded"]["beta_px"] == 24                           # D10: removed by the cleanup
    assert any("px on average" in w for w in summ["warnings"])
    t = pd.read_csv(gchain.root / s.outputs["summary_table"].path)
    assert {"alpha_ecd", "lath_thickness_intercept", "lath_thickness_minor_axis", "colony_ecd"} <= set(t["quantity"])
    row = t.set_index("quantity").loc["alpha_ecd"]
    assert row["mean"] == pytest.approx(a["ecd"]["mean"]) and row["lognormal_sigma"] > 0
    lath = pd.read_csv(gchain.root / s.outputs["alpha_grains"].path)
    assert lath["is_lath"].sum() == a["n_laths"] and (lath.loc[lath["is_lath"], "aspect"] >= 2).all()
    assert lath.loc[lath["used"], "is_edge"].eq(False).all() and lath.loc[lath["used"], "n_px"].min() >= 5
    xl = pd.read_excel(gchain.root / s.outputs["workbook"].path, sheet_name=None)
    assert list(xl) == ["Summary", "Alpha grains", "Colonies", "Parameters"]
    assert len(xl["Alpha grains"]) == len(lath)


def test_grainsize_figures(gchain):
    for k in GS_FIGURES:
        if k.startswith("beta"):
            with pytest.raises(ValueError, match="no prior-beta"):
                service.run(gchain, "grainsize.figure", {"map_id": MAP, "kind": k})
            continue
        st = service.run(gchain, "grainsize.figure", {"map_id": MAP, "kind": k})
        assert Image.open(gchain.root / st.outputs["figure"].path).size[0] > 200, k
    with pytest.raises(Exception, match="engine that computed it|unavailable"):
        service.run(gchain, "grainsize.figure", {"map_id": MAP, "kind": "alpha_ecd"}, backend="matlab")


def test_compare_oim_synthetic(gchain, tmp_path):
    """OIM grain file built from our own table (same grains) -> identical statistics."""
    s = _step(gchain, "grainsize.run")
    lath = pd.read_csv(gchain.root / s.outputs["alpha_grains"].path)
    lath = lath[lath["phase"] == 1]
    f = tmp_path / "oim_grains.txt"
    lines = ["# Column 1: Integrated Grain ID", "# Column 2: Grain Size (# of measurement points)",
             "# Column 3: Grain Size (area in square microns)", "# Column 4: Grain Size (diameter in microns)",
             "# Column 5: Edge grain (1) or not (0)"]
    lines += [f"{r.id} {r.n_px} {r.area_um2!r} {r.ecd_um!r} {int(r.is_edge)}" for r in lath.itertuples()]
    f.write_text("\n".join(lines) + "\n")
    st = service.run(gchain, "grainsize.compare_oim", {"map_id": MAP, "file": str(f)})
    r = json.loads((gchain.root / st.outputs["result"].path).read_text(encoding="utf-8"))
    assert r["ratio_mean"] == pytest.approx(1, abs=1e-6) and r["ks_2samp"]["D"] == pytest.approx(0, abs=1e-9)
    assert r["oim_rules_applied"] == [">= 5 px", "edge grains excluded"]


def test_mcp_and_replay(gchain, monkeypatch):
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(gchain.root.parent))
    r = mcp_server.grain_size(gchain.name, MAP, {"lath_min_aspect": 3.0})
    assert r["summary"]["params"]["lath_min_aspect"] == 3.0 and r["tables"]["workbook"].endswith(".xlsx")
    f = mcp_server.grain_size_figure(gchain.name, MAP, "colony_ecd_area")
    assert f["png"].endswith(".png")
    rep = service.replay(gchain)
    bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]
    assert not bad, bad


@pytest.mark.slow
def test_prior_beta_crop1000(tmp_path):
    from ti64app.project import Project

    f = sorted(CROPS.glob("*JU02*_crop1000.ang"))
    if not f:
        pytest.skip("crops missing")
    p = Project.create("gsb", base=tmp_path)
    mid = "JU02_c1000"
    service.run(p, "sample.set", sample_params(f[0], mid, 270.0))
    service.run(p, "ebsd.load", {"map_id": mid})
    service.run(p, "recon.run", {"map_id": mid, "params": {"orMode": "burgers"}})
    service.run(p, "ebsd.cleanup", {"map_id": mid})
    service.run(p, "grains.calc", {"map_id": mid})
    s = service.run(p, "grainsize.run", {"map_id": mid})
    summ = json.loads((p.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    b = summ["prior_beta"]
    assert b["or_used"] == "burgers" and b["across_bd"]["direction_deg"] == pytest.approx(0.0)
    assert b["along_bd"]["direction_deg"] == pytest.approx(90.0)                 # BD = -y (270°)
    assert 15 < b["across_bd"]["mean_e112_um"] < 30 and 15 < b["along_bd"]["mean_e112_um"] < 30
    assert b["ecd"]["n"] >= 5 and b["ecd"]["area_weighted"]["mean"] > 40
    assert any("colonies = alpha grains" in w for w in summ["warnings"])          # 15° lath partition
    pb = pd.read_csv(p.root / s.outputs["prior_beta"].path)
    assert {"caliper_along_bd_um", "mean_chord_across_bd_um"} <= set(pb.columns)
    with np.load(p.root / s.outputs["chords"].path) as z:
        assert z["across_bd_um"].size == b["across_bd"]["n_complete"]
    for k in ("beta_ecd", "beta_width", "beta_length"):
        st = service.run(p, "grainsize.figure", {"map_id": mid, "kind": k})
        assert (p.root / st.outputs["figure"].path).is_file()
