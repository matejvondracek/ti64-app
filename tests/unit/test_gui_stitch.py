"""Stitch tab (NiceGUI simulated user) on synthetic JEOL tiles: stitch sets, checklist, Arrange view, the greyed
"keep the original data bar" option, Stitch / Refine (manual layout recorded), Overlaps (difference image)."""
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from ti64app.gui import app as gui
from ti64app.gui.stitch_page import score_colour, single_column, stage_layout, tile_svg
from ti64app.modules import stitching
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
    assert score_colour(None, "manual position only", 0.3) == "#d62728"
    side = {"tiles": [{"name": "a", "image_number": 1, "x_px": 0.0, "y_px": 100.0, "width_px": 640,
                       "height_px": 480, "best_score": 0.9, "placed_by": "registration"}]}
    svg = tile_svg(side, 2.0, selected="a")
    assert 'y="50.0"' in svg and 'width="320.0"' in svg and 'stroke-width="4"' in svg


def test_single_column_from_layout(tmp_path):
    make_tiles(tmp_path, [(0.0, 0.0), (381.3, 2.2), (760.6, -1.4)])
    tl = stitching.scan(tmp_path)
    assert single_column(stage_layout(tl), tl)
    lay = stage_layout(tl)
    lay[next(iter(lay))][0] += 400                   # dragged sideways in the Arrange view
    assert not single_column(lay, tl)


@pytest.mark.anyio
async def test_stitch_page_sets_arrange_refine(tmp_path):
    folder = tmp_path / "tiles"
    folder.mkdir()
    make_tiles(folder, GRID[:4], det="BED-C")
    make_tiles(folder, GRID[:4], det="SED", seed=5)
    make_tiles(folder, [(0.0, 0.0)], det="BED-C", mag=23, start=9700)          # an overview: not stitchable
    Project.open_or_create("gst")
    async with user_simulation(main_file=MAIN) as user:
        sys.modules["ti64app.gui.app"].STATE["project"] = "gst"
        await user.open("/stitch")
        await user.should_see("Stitch SEM images")
        user.find(marker="st_source").elements.pop().set_value("folder")
        user.find(marker="st_folder").type(str(folder))
        user.find(marker="st_scan").click()
        await user.should_see("9 images")
        await user.should_see(marker="st_set_radio")
        await user.should_see("BSE 4000×")
        await user.should_see("Not stitchable")
        await user.should_see(marker="st_chk_9771")
        await user.should_see(marker="st_arrange")
        arr = user.find(marker="st_arrange").elements.pop()
        assert len(arr._props["tiles"]) == 4 and all(t["selected"] for t in arr._props["tiles"])
        # 2 x 2 grid: "keep the original data bar" is greyed out with the reason as a tooltip
        strip = user.find(marker="st_strip").elements.pop()
        keep = next(o for o in strip._props["options"] if strip._values[o["value"]] == "keep")
        assert keep["disable"] and "single column" in keep["reason"]
        # Refine: register from the positions in the Arrange view (recorded as manual_layout)
        user.find(marker="st_refine").click()
        await user.should_see(marker="st_summary_BSE", retries=400)
        await user.should_see(marker="st_pairs_BSE")
        await user.should_see(marker="st_overlap_select")
        await user.should_see(marker="st_overlap_info", retries=100)
    p = Project.open_or_create("gst")
    steps = [s for s in p.recipe().steps if s.op == "sem.stitch"]
    assert len(steps) == 1
    prm = steps[0].params
    assert prm["detector"] == "BSE" and prm["blend"] == "seam" and len(prm["files"]) == 4
    assert set(prm["manual_layout"]) == {"9771", "9772", "9773", "9774"} and prm["refine_radius"] == 0.1
