"""Phase 6 GUI: compare-backends page and running a recipe file from the recipe page (NiceGUI simulated user)."""
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from tests.conftest import sample_params
from ti64app import service
from ti64app.backends import get_backend
from ti64app.gui import app as gui
from ti64app.gui.pages import compare_table_rows
from ti64app.project import Project

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
    p = Project.open_or_create("gui6")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    return p


def _open(name):
    sys.modules["ti64app.gui.app"].STATE["project"] = name


@pytest.mark.anyio
async def test_compare_page_renders(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui6")
        await user.open("/compare")
        for m in ("cmp_op", "cmp_map", "cmp_form", "cmp_run"):
            await user.should_see(marker=m)
        await user.should_see("Compare backends")


@pytest.mark.matlab
@pytest.mark.anyio
async def test_compare_page_runs_cleanup(gproj):
    if not get_backend("matlab").is_available():
        pytest.skip("matlab backend unavailable")
    async with user_simulation(main_file=MAIN) as user:
        _open("gui6")
        await user.open("/compare")
        user.find(marker="cmp_op").elements.pop().set_value("ebsd.cleanup")
        user.find(marker="cmp_run").click()
        await user.should_see(marker="cmp_result", retries=1200)
        await user.should_see(marker="cmp_table")
    assert gproj.recipe().steps[-1].op == "compare.backends"


def test_compare_table_rows_flags():
    t = {"rows": [{"key": "n", "a": 10, "b": 10, "abs_diff": 0, "rel_diff": 0.0, "same": True},
                  {"key": "f", "a": 1.0, "b": 1.05, "abs_diff": 0.05, "rel_diff": 0.0476, "same": False},
                  {"key": "s", "a": "x", "b": "y", "abs_diff": None, "rel_diff": None, "same": False}]}
    rows = compare_table_rows(t)
    assert [r["flag"] for r in rows] == ["", "> 1 %", "> 1 %"]
    assert [r["key"] for r in compare_table_rows(t, only_diff=True)] == ["f", "s"]


@pytest.mark.anyio
async def test_recipe_page_runs_recipe_file(gproj):
    async with user_simulation(main_file=MAIN) as user:
        _open("gui6")
        Project.create("gui6_target")
        await user.open("/recipe")
        await user.should_see(marker="run_recipe_file")
    # same service call the button makes
    rep = service.run_recipe(Project.open(gproj.root.parent / "gui6_target"), service.load_recipe_file(gproj.recipe_path),
                             recorded_root=gproj.root)
    assert rep.ok and [s.op for s in rep.steps] == ["sample.set", "ebsd.load"]
