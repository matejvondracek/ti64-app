"""Fix 3 GUI parts through NiceGUI's simulated user: memory + Feedback in the top bar, KAM / GROD / GOS / GND in the
map list (computed on first use), prior-beta grains on the prior beta tab, History right-click delete."""
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from tests.conftest import sample_params
from ti64app import service
from ti64app.gui import app as gui
from ti64app.gui import dialogs
from ti64app.project import Project

from .test_gui_pages import MAIN


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    dialogs.MOCK = []
    yield
    gui.STATE["project"] = None
    dialogs.MOCK = None


@pytest.fixture
def fproj(crop300_ju02):
    p = Project.open_or_create("gui_fix3")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    return p


def _open(name):
    sys.modules["ti64app.gui.app"].STATE["project"] = name


@pytest.mark.anyio
async def test_top_bar_memory_and_feedback(fproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui_fix3")
        await user.open("/files")
        await user.should_see(marker="mem_use")
        await user.should_see("App ")
        user.find(marker="feedback_btn").click()
        await user.should_see(marker="feedback_dialog")
        await user.should_see("Open GitHub issue")
        await user.should_see("Send e-mail")
        await user.should_see("shortened to its file name")


@pytest.mark.anyio
async def test_kam_on_demand_prior_beta_grains_and_history_delete(fproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui_fix3")
        await user.open("/ebsd")
        await user.should_see(marker="map_viewer", retries=200)
        kind = user.find(marker="viewer_kind").elements.pop()
        assert "KAM · computed on first use" in kind.options.values()
        kind.set_value("kam")                                            # computed on first use
        await user.should_see("KAM done", retries=300)
    steps = fproj.recipe().steps
    assert [s.op for s in steps[-2:]] == ["grains.calc", "map.compute"] and steps[-1].params["kind"] == "kam"
    r = service.run(fproj, "recon.run", {"map_id": "JU02_c300"})
    async with user_simulation(main_file=MAIN) as user:
        _open("gui_fix3")
        await user.open("/ebsd")
        await user.should_see(marker="map_viewer", retries=200)
        user.find(marker="mtab_beta").click()
        await user.should_see("Grains and boundaries · prior β", retries=100)
        user.find(marker="run_grains").click()
        await user.should_see("prior-β grains done", retries=300)
        await user.should_see("Prior-β grains and boundaries")          # History of the prior beta tab
    g = fproj.recipe().steps[-1]
    assert g.op == "grains.calc" and g.params["source"] == r.id
    st = fproj.gui_state()
    assert st["mtab"]["JU02_c300"] == "beta" and "hagb" in st["overlays"]["JU02_c300"]["beta"]
    # History right-click > Delete: the prior-beta grains have no dependents -> deleted at once, undoable
    res = service.delete_step(fproj, g.id)
    assert res["delete"] == [g.id] and g.id not in {s.id for s in fproj.recipe().steps}
    assert service.undo(fproj)
    assert g.id in {s.id for s in fproj.recipe().steps}
