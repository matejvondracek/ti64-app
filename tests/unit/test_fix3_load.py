"""Phase 6.6 fix 3.7 / 3.8: EBSD tab load time and load progress stages.

- an old gui_state.json (layer.<map> = "phase" from before the map tabs) no longer breaks drawing the map
- a layer's cache key depends only on the steps it is drawn from (new grains / KAM / reconstruction steps do not
  recolour the IPF map, so the backend objects are not rebuilt)
- the viewer colours a cleaned map from its stored orientations without rebuilding the cleanup; colours identical
- the IPF key needs no cleanup rebuild
- progress stages are reported along the load path (only inside a channel)
"""
import dataclasses
import shutil

import numpy as np
import pytest

from tests.conftest import CROPS
from ti64app import layers as lay
from ti64app import progress, service
from ti64app.project import Project


def _crop():
    f = sorted(CROPS.glob("*JU02*_crop300.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    return f[0]


@pytest.fixture
def cleaned(tmp_path):
    p = Project.create("fix3", base=tmp_path)
    mid = p.add_files([_crop()])[0].id
    service.run(p, "ebsd.load", {"map_id": mid})
    service.run(p, "ebsd.cleanup", {"map_id": mid})
    return p, mid


def _restart(p):
    """As after an app restart: no backend objects, no in-memory layers."""
    service.release_project(p)
    lay._LAYERS.clear()
    lay._LAYOUTS.clear()
    lay._OVERLAYS.clear()


def test_gui_state_old_format_value_is_replaced(tmp_path):
    from ti64app.gui.state import GuiState

    p = Project.create("gs", base=tmp_path)
    p.save_gui_state({"layer": {"JU02": "phase"}, "overlays": {"JU02": []}})
    gs = GuiState(p)
    gs.set("layer", "JU02", "alpha", "ipf")              # crashed with 'str' object has no attribute 'get'
    gs.set("overlays", "JU02", "beta", ["beta_gb"])
    assert gs.get("layer", "JU02") == {"alpha": "ipf"}
    assert p.gui_state()["overlays"]["JU02"] == {"beta": ["beta_gb"]}


def test_layer_key_ignores_unrelated_steps(cleaned):
    p, mid = cleaned
    lp = lay.LayerParams(kind="ipf")
    k0 = lay._cache_key(p, mid, lp, "pymtex")
    g = service.run(p, "grains.calc", {"map_id": mid})
    service.run(p, "map.compute", {"map_id": mid, "kind": "kam"})
    assert lay._cache_key(p, mid, lp, "pymtex") == k0          # IPF of the cleanup: unchanged
    kk = lay._cache_key(p, mid, lay.LayerParams(kind="kam"), "pymtex")
    service.run(p, "map.compute", {"map_id": mid, "kind": "kam", "kam_order": 2})
    assert lay._cache_key(p, mid, lay.LayerParams(kind="kam"), "pymtex") != kk   # new KAM values
    ko = lay._cache_key(p, mid, lay.LayerParams(), "pymtex", overlay="hagb")
    assert lay.layer_deps(p.recipe().steps, mid, lay.LayerParams(), "hagb") == [g.id]
    service.run(p, "grains.calc", {"map_id": mid, "grains": {"hagb_deg": 10.0}})
    assert lay._cache_key(p, mid, lay.LayerParams(), "pymtex", overlay="hagb") != ko
    service.run(p, "ebsd.cleanup", {"map_id": mid, "overrides": {"noise_angle_deg": 4.0}})
    assert lay._cache_key(p, mid, lp, "pymtex") != k0          # new cleanup: new colours
    # a note on the map sheet does not recolour
    k1 = lay._cache_key(p, mid, lp, "pymtex")
    p.update_file(mid, notes="polished again")
    assert lay._cache_key(p, mid, lp, "pymtex") == k1


def test_viewer_colours_cleaned_map_without_rebuilding_cleanup(cleaned, monkeypatch):
    p, mid = cleaned
    lp = lay.LayerParams(kind="ipf")
    live = lay.get_layer(p, mid, lp).colors.copy()               # with the live cleanup object
    _restart(p)
    shutil.rmtree(p.cache_dir / "viewer")
    calls = []
    orig = service.OPS["ebsd.cleanup"].func
    monkeypatch.setitem(service.OPS, "ebsd.cleanup",
                        dataclasses.replace(service.OPS["ebsd.cleanup"], func=lambda ctx: calls.append(1) or orig(ctx)))
    with progress.channel("t"):
        L = lay.get_layer(p, mid, lp)
        lay.key_pngs(p, mid, lp)
    assert calls == []                                            # cleanup not run again
    assert np.array_equal(L.colors, live)
    stages = [s for s, _t in progress.timings("t")]
    assert stages[0] == "reading file" and "colouring map" in stages and stages[-1] == "drawing"
    assert "building grid" in stages and "repeating cleanup" not in stages


def test_load_progress_stages_and_cached_layer(cleaned):
    p, mid = cleaned
    lay.get_layer(p, mid, lay.LayerParams(kind="ipf"))
    _restart(p)
    with progress.channel("c"):
        lay.get_layer(p, mid, lay.LayerParams(kind="ipf"))
    assert [s for s, _ in progress.timings("c")] == ["cleanup from cache", "building grid", "drawing"]
    progress.clear("c")
    assert progress.get("c") is None
    progress.report("outside a channel")                        # no-op without a channel (ops, CLI, MCP)
    with progress.channel("l"):
        service.run(p, "ebsd.load", {"map_id": mid})
    assert [s for s, _ in progress.timings("l")][:1] == ["reading file"]
