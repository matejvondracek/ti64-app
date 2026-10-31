"""GUI pages through NiceGUI's simulated user (no browser). Uses the anyio pytest plugin."""
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from ti64app.gui import app as gui
from ti64app.project import Project
from ti64app.settings import load_settings

MAIN = Path(gui.__file__).with_name("__main__.py")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    yield
    gui.STATE["project"] = None


@pytest.mark.anyio
async def test_settings_page_lists_backends_and_saves():
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/settings")
        await user.should_see("Analysis backend")
        await user.should_see("pymtex: available")
        await user.should_see("matlab:")                 # available or 'unavailable — reason'
        user.find("save_settings").click()
        await user.should_see("saved to")
    assert load_settings().backend in ("pymtex", "matlab")


@pytest.mark.anyio
async def test_create_project_and_enter_sample_sheet(crop300_ju02):
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/")
        await user.should_see("Open or create a project")
        user.find("new_project").type("gui_p")
        user.find("create_project").click()
        await user.should_see("project: gui_p")
        await user.open("/sample")
        user.find("map_file").type(str(crop300_ju02))
        user.find("specimen_id").type("EBMX-1")
        user.find("map_id").type("JU02_c300")
        user.find("bd_angle").type("270")
        user.find("input_state").click()                 # not chosen yet -> saving must fail
        user.find("save_sample").click()
        await user.should_see("input state")
        user.find("input_state").elements.pop().value = "oim_cleaned"
        user.find("save_sample").click()
        await user.should_see("sample sheet JU02_c300 saved")
    p = Project.open_or_create("gui_p")
    s = p.sample("JU02_c300")
    assert s.input_state == "oim_cleaned" and s.build_direction.unit_vector == (0.0, -1.0, 0.0)
    assert [st.op for st in p.recipe().steps] == ["sample.set"]
