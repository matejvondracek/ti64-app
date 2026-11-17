"""Phase 6.7 fix 1 (pymtex, crops / synthetic): parent phase added for alpha-only maps, texture never fails for a
missing BD / position (map axes by default, unavailable directions skipped), grain dilation single iteration by
default, grain CI standardization step, grain-size figures as view state (not recipe steps), log-normal fit option
of the shown grains, map axes exact / approximate with labels."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tests.conftest import sample_params
from ti64app import service, views
from ti64app import texture as tx
from ti64app.backends.base import GrainDilationParams, ReconParams
from ti64app.sample import MapAxes

MID = "JU02_c300"


@pytest.fixture
def loaded(project, crop300_ju02):
    prm = sample_params(crop300_ju02)
    prm.pop("build_direction")                     # no BD, no position: item 2
    service.run(project, "sample.set", prm)
    service.apply(project, "ebsd.load", {"map_id": MID})
    return project


def _json(p, step, name="summary"):
    return json.loads((p.root / step.outputs[name].path).read_text(encoding="utf-8"))


# ---- item 1: beta lattice setting, parent phase added ----------------------------------------------------------------
def test_recon_params_beta_lattice_default():
    assert ReconParams().betaLattice == 3.32


def test_with_parent_phase_adds_beta_only_when_missing(crop300_ju02):
    from ti64app.backends import get_backend
    from ti64app.backends.base import LoadParams
    from ti64app.backends.pymtex_recon import phase_cs, with_parent_phase

    be = get_backend("pymtex")
    h = be.load_ebsd(LoadParams(file=crop300_ju02, input_state="oim_cleaned", phase_names={1: "Ti (beta)",
                                                                                         2: "Ti (alpha)"}))
    e = be._get(h)
    assert with_parent_phase(e) is e                                  # beta in the file: unchanged
    a_only = e.copy()                         # same phase ids, but no phase called 'Ti (beta)' (as an alpha-only file)
    from mtex import crystalFrame
    from mtex.maps.phaselist import phaseList

    a_only.CSList = phaseList([crystalFrame("6/mmm", [2.95, 2.95, 4.68], mineral="Ti (other)")
                               if f.mineral == "Ti (beta)" else f for f in e.CSList.frames])
    out = with_parent_phase(a_only, 3.30)
    assert "Ti (beta)" in out.CSList.mineralList and "Ti (beta)" not in a_only.CSList.mineralList
    cs = phase_cs(out, "Ti (beta)")
    assert np.allclose(np.asarray(cs.abc, float).ravel(), 3.30) and cs.pointGroup == "m-3m"
    assert phase_cs(a_only, "Ti (beta)") is not None                 # symmetry for colours / ODFs even if absent


# ---- items 2 and 9: frames, direction choices ------------------------------------------------------------------------
def test_frame_without_bd_and_position_offers_map_axes(loaded):
    fr = tx.frame_for(loaded, MID)
    assert fr.kind == "map" and fr.defaults == ["x", "y", "z"]
    assert set(fr.unavailable) == {"BD", "beam axis", "thickness", "width"}
    assert "Build direction" in fr.unavailable["BD"] and "Position on the specimen" in fr.unavailable["beam axis"]
    ch = {c["name"]: c for c in tx.direction_choices(loaded, MID)}
    assert ch["x"]["available"] and not ch["BD"]["available"] and ch["BD"]["why"]
    notes: list[str] = []
    got = tx.pick_directions(fr, ["BD", "beam axis", "width"], None, notes)          # the old default: never fails
    assert [n for n, _v in got] == ["x", "y", "z"] and len(notes) == 4
    assert tx.pick_directions(fr, ["map x", "custom"], (1, 1, 0), [])[0] == ("map x", (1.0, 0.0, 0.0))


def test_frame_approximate_labels_and_bd(loaded):
    loaded.update_file(MID, axes=MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T", build_axis="y"))
    sh = loaded.sample(MID)
    assert sh.build_direction is not None and sh.build_direction.unit_vector == (0.0, 1.0, 0.0)
    fr = tx.frame_for(loaded, MID)
    assert fr.mode == "approximate" and fr.labels == {"x": "L", "y": "S = BD", "z": "T"}
    assert fr.directions["S"] == fr.directions["BD"] == (0.0, 1.0, 0.0) and fr.defaults == ["L", "S", "T"]
    assert not fr.unavailable
    assert MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T", build_axis="y").text() == \
        "x = L, y = S (BD), z = T"


def test_recorded_sheet_keeps_exact_bd_out_of_approximate_mode(loaded):
    """Recording the effective sheet (before every step) must not store the BD derived from the approximate axes as
    the map's exact build direction (seen as a wrong 'Prefill from ...' offer on the Files tab)."""
    loaded.update_file(MID, axes=MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T", build_axis="y"))
    service.apply(loaded, "grains.calc", {"map_id": MID})                  # records the sheet first
    assert loaded.file(MID).build_direction is None
    assert loaded.sample(MID).build_direction.unit_vector == (0.0, 1.0, 0.0)
    loaded.update_file(MID, axes=None)                                      # back to exact: no BD entered
    assert loaded.sample(MID).build_direction is None


def test_map_axes_validation():
    with pytest.raises(ValueError):
        MapAxes(x_label="L", y_label="l", z_label="T")
    with pytest.raises(ValueError):
        MapAxes(x_label="")
    assert MapAxes().build_direction is None                         # exact mode: BD from the build-direction fields


def test_axes_change_marks_texture_stale(loaded):
    t = service.apply(loaded, "texture.run", {"map_id": MID, "halfwidth_deg": 10})
    loaded.update_file(MID, axes=MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T"))
    assert loaded.recipe().step(t.id).stale


def test_texture_and_elastic_without_bd_use_labels(loaded):
    t = service.apply(loaded, "texture.run", {"map_id": MID, "halfwidth_deg": 10})
    s = _json(loaded, t)
    assert s["ipf_dirs"] == ["x", "y", "z"] and s["frame"]["axis_labels"] == {"x": "x", "y": "y", "z": "z"}
    loaded.update_file(MID, axes=MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T", build_axis="x"))
    t2 = service.apply(loaded, "texture.run", {"map_id": MID, "halfwidth_deg": 10,
                                               "ipf_dirs": ["BD", "S", "beam axis"]})
    s2 = _json(loaded, t2)
    assert s2["ipf_dirs"] == ["BD", "S"] and any("beam axis" in w for w in s2["warnings"])
    assert s2["frame"]["labels"]["x"] == "L = BD"
    e = service.apply(loaded, "elastic.run", {"map_id": MID, "bootstrap_runs": 0})
    assert [r["direction"] for r in _json(loaded, e)["E_table"]] == ["L", "S", "T"]


def test_mcp_axes_and_directions(project, crop300_ju02, monkeypatch):
    from ti64app import mcp_server as m

    monkeypatch.setenv("TI64_PROJECTS_DIR", str(project.root.parent))
    m.set_sample_sheet(project.root.name, str(crop300_ju02), "oim_cleaned", map_id=MID, axes_mode="approximate",
                       axis_labels=["L", "S", "T"], build_axis="z")
    from ti64app.project import Project

    p = Project.open(project.root)
    assert p.sample(MID).axes.label("z") == "T"
    d = m.map_directions(project.root.name, MID)
    assert d["defaults"] == ["L", "S", "T"] and d["frame"]["labels"]["z"] == "T = BD"


# ---- items 4 and 5: cleanup -----------------------------------------------------------------------------------------
def test_dilation_single_iteration_default():
    assert GrainDilationParams().single_iteration is True


@pytest.mark.parametrize("mode", ["mean", "max"])
def test_grain_ci_standardization(loaded, mode):
    p = loaded
    service.apply(p, "ebsd.clean", {"map_id": MID, "operation": "grain_dilation", "params": {}})
    before = np.load(p.root / p.recipe().steps[-1].outputs["ebsd"].path)["prop_ci"]
    s = service.apply(p, "ebsd.clean", {"map_id": MID, "operation": "grain_ci",
                                        "params": {"mode": mode, "tolerance_deg": 5, "min_grain_px": 2}})
    rep = _json(p, s, "report")
    assert rep["operation"] == "grain_ci" and rep["mode"] == mode and 0 < rep["n_changed"] <= rep["n_in_grains"]
    assert "changed CI" in rep["summary"]
    after = np.load(p.root / s.outputs["ebsd"].path)["prop_ci"]
    assert after.shape == before.shape and int((np.abs(after - before) > 1e-9).sum()) == rep["n_changed"]
    if mode == "max":
        assert rep["ci_mean_after"] >= rep["ci_mean_before"]
    else:
        assert abs(rep["ci_mean_after"] - rep["ci_mean_before"]) < 1e-6         # the mean of means per grain area


# ---- items 6 and 7: grain-size figures --------------------------------------------------------------------------------
def test_grainsize_figure_is_a_view_not_a_step(loaded):
    p = loaded
    service.apply(p, "grains.calc", {"map_id": MID})
    service.apply(p, "grainsize.run", {"map_id": MID, "target": "alpha"})
    n = len(p.recipe().steps)
    f = service.apply(p, "grainsize.figure", {"map_id": MID, "kind": "alpha_ecd", "bin_width_um": 0.5,
                                              "lognormal_fit": True})
    assert len(p.recipe().steps) == n and f.id.startswith("v")
    assert (p.root / f.outputs["figure"].path).is_file()
    assert views.settings(p.root, MID) == {"kind": "alpha_ecd", "bin_width_um": 0.5, "min_um": None, "max_um": None,
                                           "lognormal_fit": True, "width_cm": None, "dpi": None}
    out = service.render_figure_as(p, f.id, "svg")                 # Save / Export find the view figure
    assert out.suffix == ".svg" and out.is_file()


def test_migrate_moves_figure_steps_out_of_the_recipe(loaded):
    p = loaded
    service.apply(p, "grains.calc", {"map_id": MID})
    gsr = service.apply(p, "grainsize.run", {"map_id": MID, "target": "alpha"})
    old = service.run(p, "grainsize.figure", {"map_id": MID, "kind": "alpha_ecd"}, record=False, step_id="oldfig01")
    r = p.recipe()
    r.steps.append(old)
    p.save_recipe(r)
    assert views.migrate_recipe(p.root) == 1
    assert [s.op for s in p.recipe().steps][-1] == "grainsize.run" and gsr.id in {s.id for s in p.recipe().steps}
    assert views.step(p.root, "oldfig01").op == "grainsize.figure"


def test_lognormal_fit_scaled_to_shown_bins():
    from ti64app.ops_grainsize import _hist_chart

    rng = np.random.default_rng(1)
    v = rng.lognormal(np.log(0.3), 0.6, 3000)
    off = _hist_chart(v, None, "x", "t", bin_width=0.05, x_range=(0.5, None))
    assert off.curves == [] and "shown" in off.title
    on = _hist_chart(v, None, "x", "t", bin_width=0.05, x_range=(0.5, None), fit=True)
    (xs, ys, label), = on.curves
    n_shown = int(((v >= 0.5) & (v <= on.centers[-1] + on.bar_width / 2)).sum())
    assert f"{n_shown} shown grains" in label and f"{n_shown} of 3000 shown" in on.title
    assert abs(np.trapezoid(ys, xs) / on.bar_width - on.heights.sum()) / on.heights.sum() < 0.05   # same area
    assert ys.max() < 2 * on.heights.max()                         # no longer ~3x the bars (report 2004)
