"""Phase 6.6 fix 2 (EBSD): load without sample / build direction, steps that need the build direction say so,
data-aware layer / overlay lists, grain size per map tab, histogram bin width, delete steps (undoable), replay
progress / cancel / memory, loaded-map cache, legends anchored to the map."""
import json

import numpy as np
import pytest

from tests.conftest import CROPS
from ti64app import layers as lay
from ti64app import service
from ti64app.legends import LegendItem, in_region
from ti64app.modules.grain_size import hist_edges
from ti64app.project import Project
from ti64app.sample import BuildDirection, SheetIncomplete


def _crop(name: str):
    f = sorted(CROPS.glob(f"*JU02*_{name}.ang"))
    if not f:
        pytest.skip("crops missing (python scripts/make_crops.py)")
    return f[0]


@pytest.fixture
def bare(tmp_path):
    """A project with a linked .ang and nothing entered (no sample, no input state, no build direction)."""
    p = Project.create("bare", base=tmp_path)
    m = p.add_files([_crop("crop300")])[0]
    return p, m.id


def test_load_without_sample_or_build_direction(bare):
    p, mid = bare
    sheet = p.sample(mid)
    assert sheet.specimen_id is None and sheet.build_direction is None
    assert sheet.input_state == "oim_cleaned"                      # suggested from the file name ("cleaned")
    step = service.run(p, "ebsd.load", {"map_id": mid})
    assert step.metrics["n_points"] > 0
    m = p.file(mid)
    assert m.input_state is None and m.sample_id is None          # the suggestion was not stored as user input
    # steps that do not need the build direction work
    service.run(p, "figure", {"map_id": mid, "kind": "ipf", "direction": "Z"})
    service.run(p, "grains.calc", {"map_id": mid})
    # IPF along the build direction asks for it, naming the map and the place
    with pytest.raises(SheetIncomplete) as e:
        service.run(p, "figure", {"map_id": mid, "kind": "ipf", "direction": "build"})
    assert e.value.map_id == mid and "Files tab" in str(e.value)
    # after entering it, the same step works
    p.update_file(mid, build_direction=BuildDirection(angle_deg=270))
    service.run(p, "figure", {"map_id": mid, "kind": "ipf", "direction": "build"})


def test_available_only_offers_what_exists(bare):
    p, mid = bare
    service.run(p, "ebsd.load", {"map_id": mid})
    av = lay.available(p, mid)
    assert "fit" not in av["layers"] and "prias" not in av["layers"]       # Fit constant, no PRIAS columns
    assert {"ipf", "phase", "ci", "iq"} <= set(av["layers"])
    assert av["overlays"] == [] and not av["recon"]
    service.run(p, "grains.calc", {"map_id": mid})
    assert lay.available(p, mid, "alpha")["overlays"] == ["hagb", "lagb", "interface", "burgers"]
    assert lay.available(p, mid, "beta")["overlays"] == []               # no beta overlays before reconstruction
    service.run(p, "annotations.set", {"map_id": mid, "items": [
        {"map_id": mid, "type": "crack", "xy": [[1, 1], [5, 5]]}]})
    assert "crack" in lay.available(p, mid, "alpha")["overlays"]


def test_grain_size_per_tab_and_bin_width(tmp_path):
    f = _crop("crop1000")
    p = Project.create("gs", base=tmp_path)
    mid = p.add_files([f])[0].id                                   # no build direction entered
    service.run(p, "ebsd.load", {"map_id": mid})
    service.run(p, "recon.run", {"map_id": mid, "params": {"orMode": "burgers"}})
    beta = service.run(p, "grainsize.run", {"map_id": mid, "target": "prior_beta"})
    s = json.loads((p.root / beta.outputs["summary"].path).read_text(encoding="utf-8"))
    assert s["alpha"] is None and s["prior_beta"]["ecd"]["n"] > 0
    assert s["needs"] == ["build direction"]                          # width / length along BD left out, said so
    assert "grains" not in {x.op for x in p.recipe().steps if x.op == "grains.calc"}   # no alpha segmentation needed
    fig = service.run(p, "grainsize.figure", {"map_id": mid, "kind": "beta_ecd", "bin_width_um": 5.0})
    assert fig.params["grainsize"] == beta.id
    with pytest.raises(SheetIncomplete):
        service.run(p, "grainsize.figure", {"map_id": mid, "kind": "beta_width"})
    # alpha grain size started from the alpha tab: no prior-beta part
    service.run(p, "grains.calc", {"map_id": mid, "grains": {"hagb_deg": 15.0}})
    a = service.run(p, "grainsize.run", {"map_id": mid, "target": "alpha"})
    sa = json.loads((p.root / a.outputs["summary"].path).read_text(encoding="utf-8"))
    assert sa["prior_beta"] is None and sa["alpha"]["n_used"] > 0
    assert service.run(p, "grainsize.figure", {"map_id": mid, "kind": "alpha_ecd"}).params["grainsize"] == a.id
    # the beta figure still finds the prior-beta step, not the newer alpha one
    assert service.run(p, "grainsize.figure", {"map_id": mid, "kind": "beta_ecd"}).params["grainsize"] == beta.id


def test_hist_edges_bin_width():
    e = hist_edges(np.array([0.4, 1.2, 7.9]), bin_width=2.0)
    assert np.allclose(e, [0, 2, 4, 6, 8])
    with pytest.raises(ValueError):
        hist_edges(np.array([1.0, 1e6]), bin_width=0.1)
    assert hist_edges(np.array([1.0, 2.0, 3.0])).size > 2                     # automatic as before


def test_delete_steps_with_dependents_and_undo(bare):
    p, mid = bare
    load = service.run(p, "ebsd.load", {"map_id": mid})
    g = service.run(p, "grains.calc", {"map_id": mid})
    k = service.run(p, "map.compute", {"map_id": mid, "kind": "kam"})
    plan = service.plan_delete(p, [g.id])
    assert plan["delete"] == [g.id, k.id] and plan["dependents"] == [k.id]   # KAM uses the grains
    n_before = len(p.recipe().steps)
    service.delete_steps(p, [g.id], "delete steps")
    ids = [s.id for s in p.recipe().steps]
    assert g.id not in ids and k.id not in ids and load.id in ids
    assert service.undo(p).startswith("delete steps")
    assert len(p.recipe().steps) == n_before


def test_delete_stale_keeps_steps_still_used(bare):
    p, mid = bare
    load = service.run(p, "ebsd.load", {"map_id": mid})
    g1 = service.run(p, "grains.calc", {"map_id": mid})
    r = p.recipe()
    r.mark_stale(lambda s: s.id in (load.id, g1.id))                 # e.g. superseded steps
    p.save_recipe(r)
    g2 = service.run(p, "grains.calc", {"map_id": mid, "source": load.id})   # a live step still uses the stale load
    plan = service.plan_delete(p, [load.id, g1.id], only_unused=True)
    assert plan["delete"] == [g1.id] and plan["kept"] == [load.id]
    assert g2.id not in plan["delete"]


def test_replay_progress_cancel_and_other_backend(bare, tmp_path):
    p, mid = bare
    service.run(p, "ebsd.load", {"map_id": mid})
    service.run(p, "grains.calc", {"map_id": mid})
    service.run(p, "map.compute", {"map_id": mid, "kind": "kam"})
    seen = []
    rep = service.replay(p, into=tmp_path / "r1", on_step=lambda k, n, s: seen.append((k, n, s.op)))
    assert [x[2] for x in seen] == [s.op for s in p.recipe().steps if not s.stale]
    assert rep.ok and rep.seconds > 0
    assert not [k for k in service.SESSION if k[0] == str(tmp_path / "r1")]     # scratch objects released
    # Cancel: stops before the third step
    calls = []
    rep = service.replay(p, into=tmp_path / "r3", on_step=lambda k, n, s: calls.append(k),
                         should_stop=lambda: len(calls) >= 2)
    assert rep.cancelled and len(rep.steps) == 2
    # steps recorded with another backend: not compared value by value (cheap), still ok
    steps = []
    for s in p.recipe().steps:                       # as if recorded by MATLAB: other backend, other bytes
        if s.backend:
            s = s.model_copy(update={"backend": "matlab", "outputs": {
                k: r.model_copy(update={"content_hash": "sha256:matlab"}) for k, r in s.outputs.items()}})
        steps.append(s)
    q = Project.create("other", base=tmp_path)
    rep = service.run_recipe(q, steps, backend="pymtex", recorded_root=p.root)
    st = {c.status for c in rep.checks}
    assert "other_backend" in st and "different" not in st and rep.ok


def test_loaded_map_cache(bare):
    p, mid = bare
    a = service.run(p, "ebsd.load", {"map_id": mid})
    assert "cached" not in a.metrics
    assert list((p.cache_dir / "maps").glob("pymtex_*.npz"))
    b = service.run(p, "ebsd.load", {"map_id": mid})
    assert b.metrics.get("cached") == 1.0
    assert a.outputs["ebsd"].content_hash == b.outputs["ebsd"].content_hash
    # a later step that needs the backend object rebuilds it (materialize bypasses the cache)
    g = service.run(p, "grains.calc", {"map_id": mid, "source": b.id})
    assert g.metrics["n_grains"] > 0


def test_legends_anchored_to_map():
    items = [LegendItem(kind="scale_bar", x=0.5, y=0.5, w=0.1, frame="map")]
    # whole map: unchanged
    assert in_region(items, (0, 100, 0, 50), None)[0].x == 0.5
    # right half of the map: the item keeps its place on the map -> left edge of the region, twice as wide
    r = in_region(items, (0, 100, 0, 50), (50, 0, 100, 50))[0]
    assert r.x == pytest.approx(0.0) and r.y == pytest.approx(0.5) and r.w == pytest.approx(0.2)
    assert in_region(items, (0, 100, 0, 50), (0, 0, 40, 50)) == []          # not in the exported region
    old = [LegendItem(kind="colorbar", x=0.1, y=0.2, w=0.3)]                  # saved before: image fractions
    assert in_region(old, (0, 100, 0, 50), (50, 0, 100, 50))[0].x == 0.1
