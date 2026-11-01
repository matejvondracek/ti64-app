"""Phase 5 operations (pymtex) on the JU02 1000x1000 crop: annotations, layers, export.map in every mode and format,
ebsd.crop, undo / redo, replay, MCP tools."""
import json

import numpy as np
import pytest
from PIL import Image

from tests.conftest import CROPS, sample_params
from ti64app import history, layers, mcp_server, service
from ti64app.annotations import Annotation, load_annotations, to_primitives

MAP = "JU02_c1000"


@pytest.fixture(scope="module")
def crop1000_ju02():
    f = sorted(CROPS.glob("*JU02*_crop1000.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    return f[0]


@pytest.fixture(scope="module")
def xchain(tmp_path_factory, crop1000_ju02):
    from ti64app.project import Project

    p = Project.create("export", base=tmp_path_factory.mktemp("proj"))
    service.run(p, "sample.set", sample_params(crop1000_ju02, MAP, 270.0))
    service.run(p, "ebsd.load", {"map_id": MAP})
    service.run(p, "grains.calc", {"map_id": MAP})
    service.run(p, "map.compute", {"map_id": MAP, "kind": "kam"})
    service.run(p, "recon.run", {"map_id": MAP, "params": {"orMode": "burgers"}})
    service.run(p, "annotations.set", {"map_id": MAP, "items": [
        {"map_id": MAP, "type": "arrow", "xy": [[20, 20], [60, 50]], "text": "A"},
        {"map_id": MAP, "type": "text", "xy": [[100, 15]], "text": "colony"},
        {"map_id": MAP, "type": "crack", "xy": [[10, 120], [80, 110], [150, 130]], "text": "crack 1"},
        {"map_id": MAP, "type": "roi_rect", "xy": [[100, 50], [160, 100]], "text": "R1"}]})
    return p


def _files(p, step):
    return p.root / step.outputs["export"].path, json.loads((p.root / step.outputs["sidecar"].path).read_text(encoding="utf-8"))


def test_annotations_stored_and_validated(xchain):
    a = load_annotations(xchain, MAP)
    assert [x.type for x in a] == ["arrow", "text", "crack", "roi_rect"]
    with pytest.raises(ValueError):
        service.run(xchain, "annotations.set", {"map_id": MAP, "items": [{"map_id": MAP, "type": "arrow", "xy": [[0, 0]]}]})
    with pytest.raises(ValueError):
        service.run(xchain, "annotations.set", {"map_id": MAP, "items": [{"map_id": MAP, "type": "text", "xy": [[0, 0]]}]})
    prims = to_primitives(a, 200.0)
    kinds = [p["type"] for p in prims]
    assert kinds.count("poly") == 1 and kinds.count("text") == 2      # arrow head; arrow label + text
    assert not any(p["type"] == "line" and len(p["xy"]) == 5 for p in prims)   # ROI outlines only on request
    assert any(len(p["xy"]) == 5 for p in to_primitives(a, 200.0, include_roi=True) if p["type"] == "line")


def test_available_layers_and_overlays(xchain):
    av = layers.available(xchain, MAP)
    assert {"ipf", "phase", "ci", "iq", "kam", "parent_ipf", "recon_fit", "variant", "packet"} <= set(av["layers"])
    assert {"hagb", "lagb", "interface", "burgers", "beta_gb", "crack"} <= set(av["overlays"])
    assert "gnd" not in av["layers"]


def test_layer_colours_cached_and_backend_coloured(xchain):
    lp = layers.LayerParams(kind="kam")
    L = layers.get_layer(xchain, MAP, lp)
    assert L.colors.shape == (10**6, 4) and L.colors.dtype == np.uint8
    assert L.info["clim"] == [0.0, 5.0] and len(L.info["colorbar"]) == 256 and L.info["backend"] == "pymtex"
    layers._LAYERS.clear()
    L2 = layers.get_layer(xchain, MAP, lp)                 # from cache/viewer/
    assert np.array_equal(L.colors, L2.colors) and L2.key == L.key
    P = layers.get_layer(xchain, MAP, layers.LayerParams(kind="parent_ipf"))
    assert P.info["keys"]["Ti (beta)"]["direction"] == "Z" and (P.colors[:, 3] == 255).mean() > 0.9
    O = layers.get_overlay(xchain, MAP, "crack", lp)
    assert O.index.n == 2 and O.legend[0][0] == "crack"


def test_export_native_and_scale(xchain):
    s = service.run(xchain, "export.map", {"map_id": MAP, "overlays": ["hagb", "crack"]})
    f, side = _files(xchain, s)
    with Image.open(f) as im:
        assert im.size == (1000, 1000) and im.mode == "RGB"
    assert side["image_px"] == [1000, 1000] and side["pixel_size_um"][0] == pytest.approx(0.2, abs=1e-6)
    assert side["pixel_size_um"][1] == pytest.approx(0.2 * np.sqrt(3) / 2, abs=1e-4)
    assert any("left" in n for n in side["notes"]) and side["source_steps"]["grains"]
    assert side["boundary_length_um"]["crack"] > 100 and "texts not drawn" in side["warnings"][-1]
    assert side["layer"]["source"] == s.params["layer"]["source"] and side["recipe_step"] == s.id
    s = service.run(xchain, "export.map", {"map_id": MAP, "mode": "scale", "scale": 4, "format": "tiff",
                                           "roi_id": "R1", "background": "transparent", "name": "roi4x"})
    f, side = _files(xchain, s)
    assert f.name == "roi4x.tif"
    with Image.open(f) as im:
        assert im.mode == "RGBA" and im.size == tuple(side["image_px"])
    assert side["roi_um"] == [100, 50, 160, 100] and s.params["roi"] == [100, 50, 160, 100]
    assert side["image_px"][0] == pytest.approx(60 / 0.05, abs=8)


def test_export_width_formats(xchain):
    for fmt in ("png", "tiff"):
        s = service.run(xchain, "export.map", {"map_id": MAP, "mode": "width", "width_mm": 50, "dpi": 300, "format": fmt,
                                               "overlays": ["hagb"], "title": True})
        f, side = _files(xchain, s)
        with Image.open(f) as im:
            assert im.size[0] == round(50 / 25.4 * 300) == side["expected_width_px"]
    s = service.run(xchain, "export.map", {"map_id": MAP, "mode": "width", "width_mm": 50, "dpi": 200,
                                           "layer": {"kind": "iq"}, "colorbar": False, "legend": False,
                                           "scale_bar": False, "annotations": False})
    f, side = _files(xchain, s)
    with Image.open(f) as im:                                # bare map fills the figure: aspect of the map
        assert im.size[0] == round(50 / 25.4 * 200) and im.size[1] == pytest.approx(im.size[0] * 0.866, abs=3)
    for fmt in ("svg", "pdf"):
        s = service.run(xchain, "export.map", {"map_id": MAP, "mode": "width", "width_mm": 40, "dpi": 150,
                                               "format": fmt, "layer": {"kind": "parent_ipf"}, "overlays": ["beta_gb"]})
        f, side = _files(xchain, s)
        assert f.suffix == "." + fmt and f.stat().st_size > 1000 and side["source_steps"]["recon"]
    with pytest.raises(ValueError):
        service.run(xchain, "export.map", {"map_id": MAP, "mode": "native", "format": "svg"})


def test_figure_op_draws_new_overlays(xchain):
    s = service.run(xchain, "figure", {"map_id": MAP, "kind": "ipf", "overlays": ["hagb", "beta_gb", "crack"]})
    meta = json.loads((xchain.root / s.outputs["meta"].path).read_text(encoding="utf-8"))
    assert {"HAGB ≥15°", "prior β GB", "crack"} <= set(meta["boundary_length_um"])
    assert s.params["recon"] and s.params["grains"]


def test_crop_to_new_map(xchain):
    s = service.run(xchain, "ebsd.crop", {"map_id": MAP, "new_map_id": "c_R1", "roi_id": "R1"})
    info = json.loads((xchain.root / s.outputs["crop"].path).read_text(encoding="utf-8"))
    assert info["n_rows"] % 2 == 1 or info["n_rows"] % 2 == 0
    assert info["n_points"] == info["n_rows"] * info["n_cols"] and info["n_cols"] == pytest.approx(300, abs=2)
    sheet = xchain.sample("c_R1")
    assert sheet.build_direction.unit_vector == xchain.sample(MAP).build_direction.unit_vector
    ld = service.run(xchain, "ebsd.load", {"map_id": "c_R1"})
    with np.load(xchain.root / ld.outputs["ebsd"].path) as z:
        assert z["x"].min() >= 100 - 0.2 and z["x"].max() <= 160 + 0.2      # original coordinates kept
        assert z["y"].min() >= 50 - 0.2 and z["y"].max() <= 100 + 0.2
    with pytest.raises(FileExistsError):
        service.run(xchain, "ebsd.crop", {"map_id": MAP, "new_map_id": "c_R1", "roi": [0, 0, 10, 10]})


def test_replay_reproduces_exports(xchain):
    rep = service.replay(xchain)
    bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance", "not_compared")]
    assert rep.ok, bad


def test_undo_redo(tmp_path, crop1000_ju02):
    from ti64app.project import Project

    p = Project.create("u", base=tmp_path)
    service.run(p, "sample.set", sample_params(crop1000_ju02, MAP, 270.0))
    a = Annotation(map_id=MAP, type="text", xy=[(1, 1)], text="x")
    s1 = service.run(p, "annotations.set", {"map_id": MAP, "items": [a.model_dump()]})
    assert len(load_annotations(p, MAP)) == 1
    assert service.undo(p).startswith("annotations.set")
    assert load_annotations(p, MAP) == [] and [s.op for s in p.recipe().steps] == ["sample.set"]
    assert (p.outputs_dir / s1.id).is_dir()                # kept while redo is possible
    assert service.redo(p).startswith("annotations.set") and len(load_annotations(p, MAP)) == 1
    service.undo(p)
    service.undo(p)                                        # sample sheet gone too
    assert p.samples() == [] and p.recipe().steps == []
    assert service.undo(p) is None
    service.redo(p)
    assert p.samples()[0].map_id == MAP
    service.run(p, "annotations.set", {"map_id": MAP, "items": []})    # new action drops the redo stack
    assert history.status(p)["redo"] == [] and not (p.outputs_dir / s1.id).exists()
    n = service.switch_backend(p, "matlab")
    assert p.info().backend == "matlab" and n == 0
    service.undo(p)
    assert p.info().backend == "pymtex"


def test_mcp_tools(xchain, monkeypatch):
    name = xchain.root.name
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(xchain.root.parent))
    assert {"parent_ipf"} <= set(mcp_server.viewer_layers(project=name, map_id=MAP)["layers"])
    items = mcp_server.get_annotations(project=name, map_id=MAP)
    out = mcp_server.set_annotations(project=name, map_id=MAP, items=[*items, {"type": "point", "xy": [[5, 5]], "text": "P"}])
    assert out["op"] == "annotations.set" and len(mcp_server.get_annotations(project=name, map_id=MAP)) == len(items) + 1
    assert mcp_server.undo(project=name)["undone"].startswith("annotations.set")
    assert mcp_server.redo(project=name)["redone"].startswith("annotations.set")
    e = mcp_server.export_map(project=name, map_id=MAP, kind="phase", mode="scale", scale=2, roi=[0, 0, 20, 20])
    assert e["meta"]["image_px"][0] == pytest.approx(20 / 0.1, abs=4) and e["file"].endswith(".png")
    assert mcp_server.recipe_history(project=name)["steps"][-1]["op"] == "export.map"
    c = mcp_server.crop_map(project=name, map_id=MAP, new_map_id="mcp_crop", roi=[0, 0, 20, 20])
    assert c["metrics"]["n_points"] > 9000
