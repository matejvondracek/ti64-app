"""MCP tools and GUI helpers are thin wrappers over the service layer."""
import pytest

from ti64app import mcp_server
from ti64app.gui import app as gui


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TI64_PROJECTS_DIR", str(tmp_path))
    return tmp_path


def test_mcp_tools_registered():
    import asyncio

    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert {"list_backends", "set_backend", "create_project", "set_sample_sheet", "get_sample_sheet",
            "load_map", "read_up2_header", "load_bse", "replay_project"} <= names


def test_mcp_sample_sheet_and_load(projects_dir, crop300_ju02):
    out = mcp_server.set_sample_sheet(project="p", map_file=str(crop300_ju02), specimen_id="EBMX-1",
                                      input_state="oim_cleaned", build_direction_angle_deg=270,
                                      outer_span_mm=40, inner_span_mm=20, width_mm=5, thickness_mm=3,
                                      load_N=800, map_x_axis="+X", map_y_axis="-Y")
    mid = out["map_id"]
    assert out["build_direction_unit"] == (0.0, -1.0, 0.0)
    sheet = mcp_server.get_sample_sheet(project="p", map_id=mid)
    assert sheet["bending"]["load_N"] == 800 and sheet["position"]["map_y_axis"] == "-Y"
    loaded = mcp_server.load_map(project="p", map_id=mid)
    assert loaded["backend"] == "pymtex" and loaded["metrics"]["n_points"] == 90000
    assert mcp_server.replay_project(project="p")["ok"]


def test_mcp_build_direction_is_required(projects_dir, crop300_ju02):
    with pytest.raises(ValueError):
        mcp_server.set_sample_sheet(project="p", map_file=str(crop300_ju02), specimen_id="x",
                                    input_state="raw")
    with pytest.raises(ValueError, match="4PB"):
        mcp_server.set_sample_sheet(project="p", map_file=str(crop300_ju02), specimen_id="x",
                                    input_state="raw", build_direction_angle_deg=0, outer_span_mm=40)


def test_mcp_backends_and_defaults():
    assert mcp_server.list_backends()["pymtex"] == "available"
    d = mcp_server.suggest_sample_defaults("EBM_Ti64_RI02_C_Area 1_OIM Map 1_Rescan_cleaned.ang")
    assert d == {"map_id": "RI02_C_Area1", "input_state": "oim_cleaned"}
    assert mcp_server.set_backend("pymtex")["ok"]


def test_gui_sheet_from_form():
    base = {"map_file": "EBM_Ti64_JU02_C_Area 3_x_cleaned.ang", "map_id": "", "specimen_id": "EBMX-1",
            "input_state": "oim_cleaned", "bd_mode": "angle", "bd_angle": 270}
    s = gui.sheet_from_form(base)
    assert s.map_id == "JU02_C_Area3" and s.bending is None and s.position is None
    with pytest.raises(ValueError, match="build direction"):
        gui.sheet_from_form({**base, "bd_angle": None})
    with pytest.raises(ValueError, match="4PB"):
        gui.sheet_from_form({**base, "outer_span_mm": 40})
    with pytest.raises(ValueError, match="input state"):
        gui.sheet_from_form({**base, "input_state": None})
    v = gui.sheet_from_form({**base, "bd_mode": "vector", "bd_x": 0, "bd_y": -1, "bd_z": 0,
                             "origin_X_mm": 1.5, "map_x_axis": "+X", "map_y_axis": "-Y"})
    assert v.build_direction.unit_vector == (0.0, -1.0, 0.0) and v.position.origin_X_mm == 1.5
