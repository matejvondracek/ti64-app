"""GUI analysis sections of the EBSD tab through NiceGUI's simulated user (no browser): reconstruction + figure, grain
size + figure, Settings dialog. Uses the anyio pytest plugin."""
import sys
from pathlib import Path

import pytest
from nicegui.testing.user_simulation import user_simulation

from ti64app.gui import app as gui
from ti64app.project import Project

MAIN = Path(gui.__file__).with_name("__main__.py")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    yield
    gui.STATE["project"] = None


def _loaded(name, crop):
    from tests.conftest import sample_params
    from ti64app import service

    p = Project.open_or_create(name)
    service.run(p, "sample.set", sample_params(crop))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    return p


@pytest.mark.anyio
async def test_home_page_lists_purpose():
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/")
        await user.should_see(marker="home_title")
        for t in ("EBSD maps and cleanup", "KAM / GND", "Prior-β reconstruction", "Grain size", "Stitching",
                  "Crack analysis", "4-point-bending stress mapping"):
            await user.should_see(t)
        await user.should_see(marker="home_open")


@pytest.mark.anyio
async def test_ebsd_tab_runs_cleanup(crop300_ju02):
    p = _loaded("gui_maps", crop300_ju02)
    async with user_simulation(main_file=MAIN) as user:
        sys.modules["ti64app.gui.app"].STATE["project"] = "gui_maps"
        await user.open("/ebsd")
        await user.should_see("Cleanup")
        await user.should_see(marker="map_viewer", retries=200)
        user.find(marker="add_default_cleanup").click()
        await user.should_see("Default cleanup added", retries=300)
    ops = [s.params.get("operation") for s in p.recipe().steps if s.op == "ebsd.clean"]
    assert ops == ["noise_removal", "void_fill"]                 # OIM-cleaned preset as separate steps (fix 5)


@pytest.mark.anyio
async def test_ebsd_tab_reconstruction_and_figure(crop300_ju02):
    p = _loaded("gui_recon", crop300_ju02)
    async with user_simulation(main_file=MAIN) as user:
        sys.modules["ti64app.gui.app"].STATE["project"] = "gui_recon"
        await user.open("/ebsd")
        user.find(marker="sec_recon").click()
        user.find("run_recon").click()
        await user.should_see("reconstruction done", retries=400)
        # the prior-beta MAP gets the focus (own map tab with the prior-beta boundaries of the parent grains,
        # fix 2); the numbers are on the Results tab
        await user.should_see("Prior-β map shown", retries=200)
        assert user.find(marker="mtab_beta").elements.pop().visible
        await user.should_see(marker="map_viewer", retries=200)
        assert Project.open(p.root).gui_state()["mtab"]["JU02_c300"] == "beta"
        assert Project.open(p.root).gui_state()["overlays"]["JU02_c300"]["beta"] == ["beta_gb"]
        user.find(marker="rtab_results").click()
        await user.should_see("merge sweep")
        await user.should_see(marker="recon_summary")
        user.find("run_recon_fig").click()
        await user.should_see("figure parent_ipf done", retries=200)
    assert [s.op for s in p.recipe().steps][-2:] == ["recon.run", "recon.figure"]


@pytest.mark.anyio
async def test_ebsd_tab_grainsize(crop300_ju02):
    p = _loaded("gui_gs", crop300_ju02)
    async with user_simulation(main_file=MAIN) as user:
        sys.modules["ti64app.gui.app"].STATE["project"] = "gui_gs"
        await user.open("/ebsd")
        user.find(marker="sec_grainsize").click()
        user.find("run_gs").click()
        await user.should_see("grain size done", retries=400)
        await user.should_see(marker="gs_summary")
        user.find("run_gs_fig").click()
        await user.should_see("α grain ECD (pymtex)", retries=200)
    # fix 6.7.1 item 6: drawing is display, not a recipe step; the settings are kept per map (views.json)
    assert [s.op for s in p.recipe().steps][-2:] == ["grains.calc", "grainsize.run"]
    from ti64app import views

    assert views.settings(p.root, "JU02_c300")["kind"] == "alpha_ecd"
    assert any(f["op"] == "grainsize.figure" for f in views.load(p.root)["figures"].values())
