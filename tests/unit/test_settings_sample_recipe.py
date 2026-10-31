import json

import pytest
from pydantic import ValidationError

from ti64app.project import Project
from ti64app.recipe import Recipe, RecipeStep
from ti64app.sample import FourPointBending, SampleSheet, suggest_input_state
from ti64app.settings import Settings, load_settings, settings_file_candidates


def test_settings_defaults():
    s = Settings()
    assert s.backend == "pymtex"
    assert s.figure.ipf["Ti (alpha)"].key == "ipfHKLKey"
    assert s.figure.ipf["Ti (beta)"].direction == "Z"


def test_settings_from_toml(tmp_path, monkeypatch):
    f = tmp_path / "s.toml"
    f.write_text("backend = 'matlab'\n[paths]\nmtex_path = 'C:\\tools\\mtex'\n", encoding="utf-8")
    monkeypatch.setenv("TI64_SETTINGS", str(f))
    assert settings_file_candidates()[0] == f
    s = load_settings()
    assert s.backend == "matlab" and s.paths.mtex_path == "C:\\tools\\mtex"


def test_settings_rejects_unknown_backend(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text("backend = 'octave'\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(f)


@pytest.mark.parametrize("name,state", [
    ("EBM_Ti64_JU02_C_Area 3_OIM Map 1_Rescan_Rescan_cleaned.ang", "oim_cleaned"),
    ("EBM_Ti64_JU02_C_Area 3_OIM Map 1.ang", "raw"),
])
def test_suggest_input_state(name, state):
    assert suggest_input_state(name) == state


def test_four_point_bending_validation():
    FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=5, thickness_mm=3, load_N=500)
    with pytest.raises(ValidationError):
        FourPointBending(outer_span_mm=20, inner_span_mm=40, width_mm=5, thickness_mm=3)
    with pytest.raises(ValidationError):
        FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=-1, thickness_mm=3)


def test_project_and_recipe_roundtrip(tmp_path):
    sheet = SampleSheet(name="JU02")
    m = sheet.add_map("EBM_Ti64_JU02_cleaned.ang")
    assert m.input_state == "oim_cleaned"
    p = Project.create("demo", sheet, base=tmp_path)
    assert p.sample().maps[0].file == "EBM_Ti64_JU02_cleaned.ang"
    r = p.recipe()
    r.add(RecipeStep(op="load_ebsd", params={"setting": 2}, backend="pymtex", versions={"mtex": "1.0.0"}))
    r.add(RecipeStep(op="cleanup", backend="pymtex"))
    r.mark_stale_from(1)
    r.save(p.recipe_path)
    r2 = Recipe.load(p.recipe_path)
    assert [s.stale for s in r2.steps] == [False, True]
    assert json.loads(p.recipe_path.read_text())["steps"][0]["backend"] == "pymtex"
    with pytest.raises(FileExistsError):
        Project.create("demo", sheet, base=tmp_path)
