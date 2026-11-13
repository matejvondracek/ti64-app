"""Phase 6.7 GUI: 'Texture' and 'Elastic properties' sections of the EBSD tab, the result tabs 'Pole figures',
'Inverse pole figures' and 'Elastic', and the elastic constants in Settings."""
from __future__ import annotations

import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from tests.conftest import sample_params
from ti64app import service
from ti64app.project import Project

from .test_gui_pages import MAIN


def _open(ref):
    sys.modules["ti64app.gui.app"].STATE["project"] = str(ref)


@pytest.mark.anyio
async def test_texture_and_elastic_sections(tmp_path, crop300_ju02):
    p = Project.create("g67", base=tmp_path)
    service.run(p, "sample.set", {**sample_params(crop300_ju02), "position": {"map_x_axis": "+X", "map_y_axis": "-Y"}})
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    service.run(p, "grains.calc", {"map_id": "JU02_c300"})
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/ebsd")
        await user.should_see(marker="map_viewer", retries=200)
        user.find(marker="rtab_pf").click()
        await user.should_see(marker="texture_placeholder")
        user.find(marker="sec_texture").click()
        user.find(marker="run_texture").click()
        await user.should_see("texture done", retries=300)
        await user.should_see(marker="pf_image", retries=50)
        await user.should_see("statistics are uncertain")
        await user.should_see(marker="texture_stats")
        user.find(marker="rtab_ipf").click()
        await user.should_see(marker="ipf_image")
        user.find(marker="sec_elastic").click()
        user.find(marker="elastic_bootstrap_runs").elements.pop().set_value(20)
        user.find(marker="run_elastic").click()
        await user.should_see("elastic properties done", retries=300)
        await user.should_see(marker="elastic_table", retries=50)
        await user.should_see(marker="sample_E_table")
        await user.should_see(marker="elastic_stats")
    ops = [s.op for s in Project.open(p.root).recipe().steps]
    assert ops[-2:] == ["texture.run", "elastic.run"]
    e = Project.open(p.root).recipe().steps[-1]
    assert e.params["bootstrap_runs"] == 20 and e.params["constants"]["alpha"]["doi"] == "10.1103/PhysRev.135.A482"


@pytest.mark.anyio
async def test_settings_elastic_constants(tmp_path):
    p = Project.create("g67s", base=tmp_path)
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        user.find(marker="settings_gear").click()
        await user.should_see(marker="settings_elastic")
        await user.should_see("Elastic constants (single crystal, GPa)")
        await user.should_see(marker="elastic_default_alpha")
