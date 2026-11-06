"""Reorder the steps of a map (History panel drag and drop, ti64app.reorder): dependency checks with a plain reason,
re-run from the first changed step in the new order, old results marked stale, one undo entry."""
import pytest

from tests.conftest import sample_params
from ti64app import history, reorder, service


@pytest.fixture
def chain(project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    load = service.run(project, "ebsd.load", {"map_id": "JU02_c300"})
    clean = service.run(project, "ebsd.cleanup", {"map_id": "JU02_c300"})
    grains = service.run(project, "grains.calc", {"map_id": "JU02_c300"})
    kam = service.run(project, "map.compute", {"map_id": "JU02_c300", "kind": "kam"})
    return project, [load, clean, grains, kam]


def test_check_order_refuses_with_reason(chain):
    p, (load, clean, grains, kam) = chain
    assert reorder.check_order([load, clean, grains, kam]) is None
    r = reorder.check_order([clean, load, grains, kam])
    assert r == "step 1 (Cleanup) needs a loaded map (Load map) before it"
    with pytest.raises(ValueError, match="not possible: step 1 \\(Cleanup\\) needs a loaded map"):
        reorder.reorder(p, "JU02_c300", [clean.id, load.id, grains.id, kam.id])
    assert [s.id for s in reorder.map_steps(p, "JU02_c300")] == [load.id, clean.id, grains.id, kam.id]


def test_reorder_reruns_marks_stale_and_undoes_in_one_step(chain):
    p, (load, clean, grains, kam) = chain
    n_undo = len(history.status(p)["undo"])
    # grains and KAM on the LOADED map, then the cleanup: valid (both need a loaded or cleaned map)
    res = reorder.reorder(p, "JU02_c300", [load.id, grains.id, kam.id, clean.id])
    assert res["first_changed"] == 1 and res["stale"] == [clean.id, grains.id, kam.id]
    new = reorder.map_steps(p, "JU02_c300")
    assert [s.op for s in new] == ["ebsd.load", "grains.calc", "map.compute", "ebsd.cleanup"]
    assert new[0].id == load.id and all(s.id in res["rerun"] for s in new[1:])
    # references were resolved again for the new order: the grains now come from the loaded map
    assert new[1].params["source"] == load.id
    r = p.recipe()
    assert all(r.step(i).stale for i in res["stale"])
    assert len(history.status(p)["undo"]) == n_undo + 1                 # ONE undo entry
    assert service.undo(p).startswith("reorder JU02_c300")
    assert [s.id for s in reorder.map_steps(p, "JU02_c300")] == [load.id, clean.id, grains.id, kam.id]
