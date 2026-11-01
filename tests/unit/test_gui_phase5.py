"""Phase 5 GUI pages through NiceGUI's simulated user (no browser; the Leaflet viewer itself is exercised by
scripts/gui_screenshots.py with Playwright, tests/integration/test_gui_playwright.py)."""
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from tests.conftest import sample_params
from ti64app import service
from ti64app.gui import app as gui
from ti64app.project import Project
from ti64app.settings import load_settings

from .test_gui_pages import MAIN


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    yield
    gui.STATE["project"] = None


@pytest.fixture
def gproj(crop300_ju02):
    p = Project.open_or_create("gui5")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    service.run(p, "grains.calc", {"map_id": "JU02_c300"})
    return p


def _open(name):
    sys.modules["ti64app.gui.app"].STATE["project"] = name


@pytest.mark.anyio
async def test_project_browser_lists_projects_and_maps(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/")
        await user.should_see(marker="project_table")
        await user.should_see("JU02_c300")
        await user.should_see(marker="load_JU02_c300")
        await user.should_see("Recipe history, undo / redo and replay")


@pytest.mark.anyio
async def test_viewer_panels_layer_and_export(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/viewer")
        for panel in ("panel_map", "panel_layer", "panel_key", "panel_overlays", "panel_tools", "panel_export"):
            await user.should_see(marker=panel)
        await user.should_see(marker="map_viewer", retries=200)           # layer coloured, viewer created
        await user.should_see("coloured by pymtex")
        await user.should_see(marker="ov_hagb")
        user.find("ov_hagb").click()
        user.find("run_export").click()
        await user.should_see("exported JU02_c300_ipf.png", retries=300)
        await user.should_see(marker="exp_result")
    steps = gproj.recipe().steps
    assert steps[-1].op == "export.map" and steps[-1].params["overlays"] == ["hagb"]
    assert steps[-1].params["layer"]["ipf_keys"]["Ti (alpha)"]["key"] == "ipfHKLKey"


@pytest.mark.anyio
async def test_viewer_drawing_probe_measure_events(gproj):
    from ti64app.annotations import load_annotations

    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/viewer")
        await user.should_see(marker="map_viewer", retries=200)
        v = user.find("map_viewer")
        v.trigger("shape", {"type": "arrow", "xy": [[2, 2], [10, 8]]})
        await user.should_see("arrow added", retries=100)
        v.trigger("shape", {"type": "roi_rect", "xy": [[5, 5], [25, 20]]})
        await user.should_see("roi_rect added", retries=100)
        await user.should_see("roi_rect ROI1")
        v.trigger("shape", {"type": "text", "xy": [[3, 3]]})                 # no text entered yet
        await user.should_see("enter the text first")
        v.trigger("probe", {"type": "probe", "xy": [[10.0, 10.0]]})
        await user.should_see("point ")
        v.trigger("measure", {"type": "measure", "xy": [[0, 0], [3, 4]]})
        await user.should_see("distance 5.00 µm")
    a = load_annotations(gproj, "JU02_c300")
    assert [x.type for x in a] == ["arrow", "roi_rect"] and a[1].text == "ROI1"
    assert [s.op for s in gproj.recipe().steps][-2:] == ["annotations.set", "annotations.set"]


@pytest.mark.anyio
async def test_recipe_page_undo_redo(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/recipe")
        await user.should_see(marker="recipe_table")
        await user.should_see("undo: grains.calc")
        user.find("undo").click()
        await user.should_see("undone: grains.calc", retries=100)
    assert [s.op for s in gproj.recipe().steps] == ["sample.set", "ebsd.load"]
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/recipe")
        user.find("redo").click()
        await user.should_see("redone: grains.calc", retries=100)
    assert gproj.recipe().steps[-1].op == "grains.calc"


@pytest.mark.anyio
async def test_ops_page_builds_forms_and_runs(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ops")
        await user.should_see("Parameter panels for every operation")
        await user.should_see("overrides")                                  # ebsd.cleanup form
        user.find("ops_run").click()
        await user.should_see(marker="ops_result", retries=300)
    assert gproj.recipe().steps[-1].op == "ebsd.cleanup"


def test_param_form_types():
    """Every op's params model can be turned into a form (no unsupported field types)."""
    import typing

    from ti64app.gui.pages import _unwrap
    for name, spec in service.OPS.items():
        for f, info in spec.params.model_fields.items():
            tp, _ = _unwrap(info.annotation)
            assert tp is not None, (name, f)
            if typing.get_origin(tp) is typing.Literal:
                assert typing.get_args(tp)


@pytest.mark.anyio
async def test_settings_page_saves_style_and_paths():
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/settings")
        for c in ("settings_backend", "settings_ipf", "settings_style", "settings_paths"):
            await user.should_see(marker=c)
        await user.should_see("literature_dir")
        user.find("literature_dir").clear().type("C:/elsewhere/literature")
        user.find("save_settings").click()
        await user.should_see("saved to")
    assert load_settings().paths.literature_dir == "C:/elsewhere/literature"
