"""Phase 6.5 GUI: /stitch page (NiceGUI simulated user) on synthetic JEOL tiles."""
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from ti64app.gui import app as gui
from ti64app.gui.stitch_page import score_colour, tile_svg
from ti64app.project import Project

from .test_gui_pages import MAIN
from .test_stitching import GRID, make_tiles


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    yield
    gui.STATE["project"] = None


def test_score_colours_and_svg():
    assert score_colour(0.9, "registration", 0.3) == "#2ca02c"
    assert score_colour(0.4, "registration", 0.3) == "#ff7f0e"
    assert score_colour(None, "stage only", 0.3) == "#d62728"
    side = {"tiles": [{"name": "a", "image_number": 1, "x_int_px": 0, "y_int_px": 100, "width_px": 640,
                       "height_px": 480, "best_score": 0.9, "placed_by": "registration"}]}
    svg = tile_svg(side, 2.0, selected="a")
    assert 'y="50.0"' in svg and 'width="320.0"' in svg and 'stroke-width="4"' in svg


@pytest.mark.anyio
async def test_stitch_page_scan_and_stitch(tmp_path):
    folder = tmp_path / "tiles"
    folder.mkdir()
    make_tiles(folder, GRID[:4], det="BED-C")
    make_tiles(folder, GRID[:4], det="SED", seed=5)
    Project.open_or_create("gst")
    async with user_simulation(main_file=MAIN) as user:
        sys.modules["ti64app.gui.app"].STATE["project"] = "gst"
        await user.open("/stitch")
        await user.should_see("Stitch SEM images")
        user.find(marker="st_folder").type(str(folder))
        user.find(marker="st_scan").click()
        await user.should_see("Detectors: BSE 4, SE 4")
        await user.should_see(marker="st_layout")
        user.find(marker="st_strip").elements.pop().set_value("generate")
        user.find(marker="st_run").click()
        await user.should_see(marker="st_summary_BSE", retries=300)
        await user.should_see(marker="st_result_BSE")
        await user.should_see(marker="st_export_BSE")
    p = Project.open_or_create("gst")
    steps = [s for s in p.recipe().steps if s.op == "sem.stitch"]
    assert len(steps) == 1 and steps[0].params["info_strip"] == "generate" and steps[0].params["detector"] == "BSE"
