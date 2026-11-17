"""Phase 6.6 fix 5 (pymtex, crops / synthetic): reference frames (Euler <-> quaternion, setting 2, CartesianAlignment),
cleanup as separate chained steps, tuning replaces instead of appending, default cleanup, grain dilation, void-fill
transparency, status badges, indexing-method label, probe, not-indexed highlight."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tests.conftest import sample_params
from ti64app import badges, frame, history, reorder, service
from ti64app.backends.hexgrid import dilation_fill, dilation_sweeps, neighbour_table

MID = "JU02_c300"
# JU04_area4 row 86, column 305 (OIM status bar: Euler (147.4, 34.1, 98.4), x,y 61.00,14.90, IQ 78013.4, CI 0.436)
PROBE_EULER_RAD = (2.57265, 0.59602, 1.71747)
PROBE_QUAT_APP = (-0.10283168, -0.93469526, -0.20033208, 0.27502268)     # what both backends hold (setting 2)


# ---- frames --------------------------------------------------------------------------------------------------------
def test_euler_quat_roundtrip_and_setting2_probe():
    e = np.array(PROBE_EULER_RAD)
    assert np.allclose(np.degrees(frame.quat2euler(frame.euler2quat(*e))), np.degrees(e), atol=1e-9)
    q = frame.app_quat_from_file_euler(e, 2)
    assert frame.rotation_angle_deg(q, PROBE_QUAT_APP) < 1e-5
    assert np.allclose(np.degrees(frame.quat2euler(q)), (302.598, 145.851, 278.404), atol=1e-3)
    assert np.allclose(np.degrees(e), (147.4, 34.1, 98.4), atol=0.06)                 # = OIM status bar
    rng = np.random.default_rng(0)
    E = rng.uniform([0, 0, 0], [2 * np.pi, np.pi, 2 * np.pi], (200, 3))
    assert np.allclose(frame.rotation_angle_deg(frame.euler2quat(*frame.quat2euler(frame.euler2quat(*E.T)).T),
                                                frame.euler2quat(*E.T)), 0, atol=1e-4)


def test_oim_axes_of_setting2():
    """app x = -A2, y = -A1, z = -A3: the setting-2 rotation maps the OIM axes onto minus the app axes."""
    a, b, c, d = frame.SETTING_ROT[2]
    R = np.array([[a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)], [2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)],
                  [2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d]])
    A1, A2, A3 = np.eye(3)
    assert np.allclose(R @ A2, [-1, 0, 0]) and np.allclose(R @ A1, [0, -1, 0]) and np.allclose(R @ A3, [0, 0, -1])
    assert frame.axis_text(2)["x"].startswith("app x = -A2")


@pytest.mark.parametrize("value,ok", [("a || x, c* || z", True), ("\ta || x,  c* || z ", True),
                                      ("a* || x, c || z", False), ("b || x, c* || z", False), (None, True)])
def test_cartesian_alignment_check(value, ok):
    assert (frame.check_alignment(value) is None) == ok


def test_load_records_frame(project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    load = service.run(project, "ebsd.load", {"map_id": MID})
    fr = json.loads((project.root / load.outputs["frame"].path).read_text(encoding="utf-8"))
    assert fr["setting"] == 2 and fr["oim_axes"]["z"].startswith("app z = -A3")
    assert fr["warning"] is None
    assert isinstance(fr["crystal_frame"], dict) and any("X||a" in v for v in fr["crystal_frame"].values())
    assert not any("X||a*" in v for v in fr["crystal_frame"].values())


# ---- cleanup chain + tuning -----------------------------------------------------------------------------------------
@pytest.fixture
def loaded(project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    load = service.apply(project, "ebsd.load", {"map_id": MID})
    return project, load


def _clean(p, op, **prm):
    return service.apply(p, "ebsd.clean", {"map_id": MID, "operation": op, "params": prm})


def _map_steps(p):
    return [s for s in p.recipe().steps if s.params.get("map_id") == MID and s.op != "sample.set" and not s.stale]


def test_clean_steps_chain_on_previous_output(loaded):
    p, load = loaded
    a = _clean(p, "ci_threshold", ci_threshold=0.2)
    b = _clean(p, "noise_removal")
    c = _clean(p, "void_fill", max_void_px=6)
    assert a.params["source"] == load.id and b.params["source"] == a.id and c.params["source"] == b.id
    g = service.apply(p, "grains.calc", {"map_id": MID})
    assert g.params["source"] == c.id                                 # analysis uses the end of the chain
    rep = json.loads((p.root / c.outputs["report"].path).read_text(encoding="utf-8"))
    assert rep["n_filled"] + rep["n_unfilled"] == rep["n_void_px"]
    assert rep["n_not_indexed_before"] == rep["n_void_px"] + rep["n_left_large_void_px"]
    assert "voids larger than the maximum void size (6 px)" in rep["summary"]


def test_tuning_replaces_and_undo_restores(loaded):
    p, load = loaded
    a = _clean(p, "noise_removal", noise_max_indexed_px=2)
    b = _clean(p, "noise_removal", noise_max_indexed_px=5)              # tuning: replaces a
    ids = [s.id for s in _map_steps(p)]
    assert ids == [load.id, b.id] and b.params["source"] == load.id
    assert b.params["params"]["noise_max_indexed_px"] == 5
    assert "replaces" in history.status(p)["undo"][-1]
    service.undo(p)
    assert [s.id for s in _map_steps(p)] == [load.id, a.id]
    assert p.recipe().step(a.id).params["params"]["noise_max_indexed_px"] == 2
    c = _clean(p, "void_fill")                                          # other operation: appended
    d = _clean(p, "noise_removal")                                      # not directly after itself: appended
    assert [s.id for s in _map_steps(p)] == [load.id, a.id, c.id, d.id]


def test_load_again_and_analysis_steps_replace(loaded):
    p, load = loaded
    again = service.apply(p, "ebsd.load", {"map_id": MID})
    assert [s.op for s in _map_steps(p)] == ["ebsd.load"] and _map_steps(p)[0].id == again.id
    g1 = service.apply(p, "grains.calc", {"map_id": MID, "grains": {"hagb_deg": 15}})
    g2 = service.apply(p, "grains.calc", {"map_id": MID, "grains": {"hagb_deg": 10}})
    assert [s.id for s in _map_steps(p)] == [again.id, g2.id] and g1.id not in [s.id for s in p.recipe().steps]
    k = service.apply(p, "map.compute", {"map_id": MID, "kind": "kam"})
    gnd = service.apply(p, "map.compute", {"map_id": MID, "kind": "grod"})      # other kind: appended
    k2 = service.apply(p, "map.compute", {"map_id": MID, "kind": "grod"})       # same kind: replaces
    assert [s.id for s in _map_steps(p)] == [again.id, g2.id, k.id, k2.id] and gnd.id != k2.id


def test_existing_recipes_and_replay_stay_exact(loaded):
    p, load = loaded
    service.run(p, "ebsd.cleanup", {"map_id": MID})                     # old combined cleanup still runs
    _clean(p, "noise_removal")
    _clean(p, "noise_removal", noise_angle_deg=4.0)
    rep = service.replay(p)
    assert rep.ok, [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]


def test_reorder_replays_the_chain(loaded):
    p, load = loaded
    a = _clean(p, "ci_threshold", ci_threshold=0.2)
    b = _clean(p, "noise_removal")
    res = reorder.reorder(p, MID, [load.id, b.id, a.id])
    new = reorder.map_steps(p, MID)
    assert [s.params.get("operation") for s in new[1:]] == ["noise_removal", "ci_threshold"]
    assert new[1].params["source"] == load.id and new[2].params["source"] == new[1].id
    assert res["stale"] == [a.id, b.id]


def test_default_cleanup_preset_steps_one_undo(project, crop300_ju02):
    sp = {**sample_params(crop300_ju02), "input_state": "raw"}
    service.run(project, "sample.set", sp)
    service.run(project, "ebsd.load", {"map_id": MID})
    n = len(history.status(project)["undo"])
    steps = service.add_default_cleanup(project, MID)
    assert [s.params["operation"] for s in steps] == ["ci_threshold", "noise_removal", "void_fill"]
    assert steps[1].params["source"] == steps[0].id
    assert len(history.status(project)["undo"]) == n + 1


# ---- grain dilation ------------------------------------------------------------------------------------------------
def _hexgrid(h=40, w=50):
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    xx += (yy % 2) * 0.5
    return xx.ravel(), (yy * np.sqrt(3) / 2).ravel()


def test_numba_dilation_equals_numpy_fill():
    x, y = _hexgrid()
    nbr = neighbour_table(x, y, np.ones(x.size, bool), True)
    rng = np.random.default_rng(3)
    for frac in (0.2, 0.6, 0.95):
        gid = rng.integers(1, 25, x.size)
        ok = rng.random(x.size) > frac
        a, _ = dilation_fill(~ok, gid.copy(), ok, nbr)
        b, counts = dilation_sweeps(~ok, gid.copy(), ok, nbr)
        assert np.array_equal(a, b) and sum(counts) == int((b[~ok] >= 0).sum())
        c, one = dilation_sweeps(~ok, gid.copy(), ok, nbr, max_iter=1)
        assert len(one) == 1 and one[0] == counts[0]


def test_grain_dilation_step(loaded):
    p, load = loaded
    _clean(p, "ci_threshold", ci_threshold=0.3)
    d = _clean(p, "grain_dilation", single_iteration=False)
    rep = json.loads((p.root / d.outputs["report"].path).read_text(encoding="utf-8"))
    assert rep["n_changed"] == sum(rep["changed_per_iteration"]) and rep["n_iterations"] >= 1
    assert rep["n_changed"] + rep["n_unchanged"] == rep["n_candidates"]
    one = _clean(p, "grain_dilation", single_iteration=True, multi_row=True)          # tuning replaces
    r1 = json.loads((p.root / one.outputs["report"].path).read_text(encoding="utf-8"))
    assert r1["n_iterations"] == 1 and r1["n_changed"] <= rep["n_changed"]
    assert "iteration" in r1["summary"]


# ---- badges --------------------------------------------------------------------------------------------------------
def test_badges_rule(loaded):
    p, load = loaded
    assert badges.state(p, None, {"x": 1}, {"x": 2})[0] == ""                 # never used: no badge
    g = service.apply(p, "grains.calc", {"map_id": MID})
    rec = {"hagb_deg": 15.0, "min_px": 1}
    assert badges.state(p, g, {"hagb_deg": 15, "min_px": 1}, rec)[0] == "applied"
    assert badges.state(p, g, {"hagb_deg": 10, "min_px": 1}, rec)[0] == "changed"
    _clean(p, "noise_removal")                                                # upstream changed
    kind, why = badges.state(p, p.recipe().step(g.id), {"hagb_deg": 15, "min_px": 1}, rec)
    assert kind == "stale" and "map changed" in why
    assert badges.same(None, "") and badges.same(2, 2.0) and not badges.same(True, False)


# ---- indexing label ------------------------------------------------------------------------------------------------
def _ang(path, names):
    path.write_text("".join(f"# Phase {i}\n# MaterialName  \t{n}\n# Symmetry              {s}\n"
                            for i, (n, s) in enumerate(names, start=1))
                    + "# GRID: HexGrid\n0 0 0 0 0 1 1 1 0 0\n", encoding="latin-1")
    return path


def test_indexing_question_rules(project, tmp_path):
    from ti64app.sample import indexing_label, needs_indexing_question, reindex_hint

    old = _ang(tmp_path / "map_old.ang", [("Ti (alpha)", 62), ("Ti (beta)", 43)])
    apex = _ang(tmp_path / "map_apex.ang", [("Ti (Titanium, alpha)", 62)])
    rescan = _ang(tmp_path / "Area 1_RESCAN.ang", [("Ti (Titanium, alpha)", 62)])
    assert "phase names" in reindex_hint(old) and reindex_hint(apex) is None and "Rescan" in reindex_hint(rescan)
    ms = project.add_files([old, apex, rescan])
    assert [bool(needs_indexing_question(m)) for m in ms] == [True, False, True]
    n_steps = len(project.recipe().steps)
    m = project.update_file(ms[0].id, indexing="spherical", indexing_asked=True)
    assert indexing_label(m) == "Spherical" and needs_indexing_question(m) is None
    m = project.update_file(ms[2].id, indexing=None, indexing_asked=True)          # 'Not sure': asked, no label
    assert indexing_label(m) is None and needs_indexing_question(m) is None
    assert len(project.recipe().steps) == n_steps                                   # metadata only


# ---- probe + highlight ---------------------------------------------------------------------------------------------
def test_probe_text(loaded):
    from ti64app.probe import probe, probe_text

    p, load = loaded
    d = probe(p, MID, load.id, 305)
    assert (d["row"], d["col"]) == (0, 305) or d["row"] == 1
    t = probe_text(d)
    assert "file Euler (" in t and "as in OIM" in t and "app frame (setting 2) Euler" in t and "CI " in t
    e = frame.file_euler(p.sample(MID).map_file, p.cache_dir)
    assert np.allclose(d["euler_file_deg"], np.degrees(e[305]))
    assert frame.rotation_angle_deg(frame.app_quat_from_file_euler(e[305]), d["quat"]) < 1e-4


def test_highlight_not_indexed(loaded):
    from ti64app import layers as lay

    p, load = loaded
    c = _clean(p, "ci_threshold", ci_threshold=0.5)
    L = lay.get_layer(p, MID, lay.LayerParams(kind="phase", highlight_ni=True))
    with np.load(p.root / c.outputs["ebsd"].path) as z:
        ni = z["phase"] == 0
    assert ni.any() and (L.colors[ni] == lay.NI_RGBA).all() and not (L.colors[~ni] == lay.NI_RGBA).all(axis=1).any()
    assert ("not indexed", "#ff00ff") in [tuple(x) for x in L.info["legend"]]
