"""Viewer features (phase 5) inside the phase 6.6 EBSD tab, through NiceGUI's simulated user (no browser; the Leaflet
viewer itself is exercised by scripts/gui_screenshots.py with Playwright, tests/integration/test_gui_playwright.py)."""
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
def gproj(crop300_ju02):
    p = Project.open_or_create("gui5")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    service.run(p, "grains.calc", {"map_id": "JU02_c300"})
    return p


def _open(name):
    sys.modules["ti64app.gui.app"].STATE["project"] = name


@pytest.mark.anyio
async def test_viewer_layer_overlay_and_export_dialog(gproj, tmp_path):
    target = tmp_path / "out" / "EBMX-1_JU02_c300_ipf.png"
    target.parent.mkdir()
    dialogs.MOCK[:] = [str(target)]                             # Windows save dialog
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ebsd")
        await user.should_see(marker="map_viewer", retries=200)
        await user.should_see("coloured by pymtex")
        user.find(marker="viewer_overlays").click()                       # overlay menu (columns)
        user.find(marker="ov_hagb").elements.pop().set_value(True)
        await user.should_see("Overlays (1)")
        user.find(marker="export_btn").click()
        await user.should_see(marker="export_dialog")
        user.find(marker="exp_browse").click()
        await user.should_see(marker="exp_fmt")
        user.find(marker="run_export").click()
        await user.should_see("Exported EBMX-1_JU02_c300_ipf.png", retries=300)
    from ti64app import exportlog

    assert all(s.op != "export.map" for s in gproj.recipe().steps)       # exports are not recipe steps (fix 3.4)
    e = exportlog.load(gproj.root)[-1]                                    # ... but logged
    assert e.op == "export.map" and e.params["overlays"] == ["hagb"] and e.file == str(target)
    assert e.params["layer"]["ipf_keys"]["Ti (alpha)"]["key"] == "ipfHKLKey"
    assert target.is_file() and target.with_suffix(".json").is_file()
    assert gproj.gui_state()["export"]["map"]["format"] == "png"           # last choices remembered
    assert gproj.gui_state()["export_folder"] == str(target.parent)       # last folder per project (fix 3.3)
    assert [it["kind"] for it in e.params["legends"]] == ["scale_bar"]   # as shown on the map (WYSIWYG)
    assert gproj.gui_state()["overlays"]["JU02_c300"] == {"alpha": ["hagb"]}         # per map tab


@pytest.mark.anyio
async def test_viewer_drawing_probe_measure_events(gproj):
    from ti64app.annotations import load_annotations

    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ebsd")
        await user.should_see(marker="map_viewer", retries=200)
        v = user.find("map_viewer")
        v.trigger("shape", {"type": "arrow", "xy": [[2, 2], [10, 8]]})
        await user.should_see("arrow added", retries=100)
        v.trigger("shape", {"type": "roi_rect", "xy": [[5, 5], [25, 20]]})
        await user.should_see("roi_rect added", retries=100)
        await user.should_see("roi_rect ROI1")
        v.trigger("shape", {"type": "text", "xy": [[3, 3]]})                 # no text entered yet
        await user.should_see("Enter the text first")
        v.trigger("probe", {"type": "probe", "xy": [[10.0, 10.0]]})
        await user.should_see("point ")
        v.trigger("measure", {"type": "measure", "xy": [[0, 0], [3, 4]]})
        await user.should_see("distance 5.00 µm")
        v.trigger("view", {"x0": 1.0, "x1": 20.0, "y0": 2.0, "y1": 15.0, "zoom": 2})
    a = load_annotations(gproj, "JU02_c300")
    assert [x.type for x in a] == ["arrow", "roi_rect"] and a[1].text == "ROI1"
    assert [s.op for s in gproj.recipe().steps][-2:] == ["annotations.set", "annotations.set"]
    assert gproj.gui_state()["view"]["JU02_c300"]["x1"] == 20.0                 # restored on reopen


@pytest.mark.anyio
async def test_history_undo_redo(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ebsd")
        await user.should_see(marker="history_panel")                     # right of the map
        await user.should_see(marker="recipe_table")
        await user.should_see("undo: grains.calc")
        user.find("undo").click()
        await user.should_see("undone: grains.calc", retries=100)
    assert [s.op for s in gproj.recipe().steps] == ["sample.set", "ebsd.load"]
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ebsd")
        await user.should_see(marker="recipe_table")
        user.find("redo").click()
        await user.should_see("redone: grains.calc", retries=100)
    assert gproj.recipe().steps[-1].op == "grains.calc"


@pytest.mark.anyio
async def test_any_operation_section_runs(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui5")
        await user.open("/ebsd")
        user.find(marker="sec_ops").click()
        await user.should_see("Parameter panels for every operation")
        await user.should_see("overrides")                                  # ebsd.cleanup form
        user.find("ops_run").click()
        await user.should_see(marker="ops_result", retries=300)
    assert gproj.recipe().steps[-1].op == "ebsd.cleanup"


def test_param_form_types():
    """Every op's params model can be turned into a form (no unsupported field types)."""
    import typing

    from ti64app.gui.history_panel import _unwrap
    for name, spec in service.OPS.items():
        for f, info in spec.params.model_fields.items():
            tp, _ = _unwrap(info.annotation)
            assert tp is not None, (name, f)
            if typing.get_origin(tp) is typing.Literal:
                assert typing.get_args(tp)
