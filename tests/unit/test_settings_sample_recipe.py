import json
import math

import pytest
from pydantic import ValidationError

from ti64app.project import Project, list_projects
from ti64app.recipe import Recipe, RecipeStep
from ti64app.sample import (BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file,
                            suggest_input_state)
from ti64app.settings import Settings, load_settings, save_preferences, settings_file_candidates

JU02 = "EBM_Ti64_JU02_C_Area 3_OIM Map 1_Rescan_Rescan_cleaned.ang"


# ---- settings ---------------------------------------------------------------------------------
def test_settings_defaults():
    s = Settings()
    assert s.backend == "pymtex"
    assert s.figure.ipf["Ti (alpha)"].key == "ipfHKLKey"


def test_settings_from_toml_and_preferences(tmp_path, monkeypatch):
    f = tmp_path / "s.toml"
    f.write_text("[paths]\nmtex_path = 'C:\\tools\\mtex'\n", encoding="utf-8")
    monkeypatch.setenv("TI64_SETTINGS", str(f))
    assert settings_file_candidates()[0] == f
    s = load_settings()
    assert s.paths.mtex_path == "C:\\tools\\mtex" and s.backend == "pymtex"
    s.backend = "matlab"
    s.figure.dpi = 300
    p = save_preferences(s)
    assert "mtex_path" not in p.read_text()          # machine paths never go to preferences
    s2 = load_settings()
    assert (s2.backend, s2.figure.dpi, s2.paths.mtex_path) == ("matlab", 300, "C:\\tools\\mtex")


def test_settings_rejects_unknown_backend(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text("backend = 'octave'\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(f)


# ---- sample sheet -----------------------------------------------------------------------------
@pytest.mark.parametrize("name,state", [(JU02, "oim_cleaned"), ("EBM_Ti64_JU02_C_Area 3_OIM Map 1.ang", "raw")])
def test_suggest_input_state(name, state):
    assert suggest_input_state(name) == state


def test_map_id_from_file():
    assert map_id_from_file(JU02) == "JU02_C_Area3"
    assert map_id_from_file("weird name!.ang") == "weird_name_"


@pytest.mark.parametrize("angle,vec", [(0, (1, 0, 0)), (90, (0, 1, 0)), (270, (0, -1, 0)), (45, (2**-0.5, 2**-0.5, 0))])
def test_build_direction_angle_convention(angle, vec):
    """Angle from map +x toward +y (decisions D4); JU02 build direction S = -y = 270 deg."""
    u = BuildDirection(angle_deg=angle).unit_vector
    assert all(math.isclose(a, b, abs_tol=1e-12) for a, b in zip(u, vec))


def test_build_direction_required_and_exclusive():
    with pytest.raises(ValidationError):
        BuildDirection()
    with pytest.raises(ValidationError):
        BuildDirection(angle_deg=0, vector=(1, 0, 0))
    with pytest.raises(ValidationError):
        BuildDirection(vector=(0, 0, 0))
    assert BuildDirection(vector=(0, -2, 0)).unit_vector == (0.0, -1.0, 0.0)
    with pytest.raises(ValidationError):            # sample sheet without build direction
        SampleSheet(map_id="a", specimen_id="s", map_file="f.ang", input_state="raw")


def test_four_point_bending_and_position_validation():
    FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=5, thickness_mm=3, load_N=500)
    with pytest.raises(ValidationError):
        FourPointBending(outer_span_mm=20, inner_span_mm=40, width_mm=5, thickness_mm=3)
    with pytest.raises(ValidationError):
        FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=-1, thickness_mm=3)
    with pytest.raises(ValidationError):
        MapPosition(map_x_axis="+X", map_y_axis="-X")


def test_sample_sheet_rejects_unsafe_id_and_empty_specimen():
    bd = BuildDirection(angle_deg=0)
    with pytest.raises(ValidationError):
        SampleSheet(map_id="../x", specimen_id="s", map_file="f", input_state="raw", build_direction=bd)
    with pytest.raises(ValidationError):
        SampleSheet(map_id="x", specimen_id="", map_file="f", input_state="raw", build_direction=bd)


# ---- project / recipe -------------------------------------------------------------------------
def test_project_and_recipe_roundtrip(tmp_path):
    p = Project.create("demo", base=tmp_path)
    assert list_projects(tmp_path) == ["demo"]
    r = p.recipe()
    r.add(RecipeStep(op="ebsd.load", params={"map_id": "A"}, backend="pymtex", versions={"mtex": "1.0.0"}))
    r.add(RecipeStep(op="up2.header", params={"file": "x"}))
    p.save_recipe(r)
    r2 = Recipe.load(p.recipe_path)
    assert [s.op for s in r2.steps] == ["ebsd.load", "up2.header"]
    assert json.loads(p.recipe_path.read_text())["steps"][0]["versions"]["mtex"] == "1.0.0"
    # switching the backend marks only backend steps stale
    assert p.set_backend("matlab") == 1
    assert [s.stale for s in p.recipe().steps] == [True, False]
    assert p.set_backend("matlab") == 0
    with pytest.raises(FileExistsError):
        Project.create("demo", base=tmp_path)
    with pytest.raises(KeyError, match="sample sheet"):
        p.sample("nope")
