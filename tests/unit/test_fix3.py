"""Fix 3 (third user test): export log instead of export steps, figure save / export, feedback report, memory,
Pictures folder, history delete with dependents."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tests.conftest import sample_params


# ---- export log (fix 3.4) -------------------------------------------------------------------------------
@pytest.fixture
def loaded(project, crop300_ju02):
    from ti64app import service

    service.run(project, "sample.set", sample_params(crop300_ju02))
    service.run(project, "ebsd.load", {"map_id": "JU02_c300"})
    return project


def test_export_is_not_a_recipe_step(loaded, tmp_path):
    from ti64app import exportlog, service

    n = len(loaded.recipe().steps)
    target = tmp_path / "out" / "ju02_ipf.png"
    step, e = service.export(loaded, "export.map", {"map_id": "JU02_c300", "layer": {"kind": "ipf"}}, target=target)
    assert len(loaded.recipe().steps) == n                     # not recorded
    assert target.is_file() and target.with_suffix(".json").is_file()
    assert (loaded.root / e.project_file).is_file() and e.project_file.startswith("exports/")
    log = exportlog.load(loaded.root)
    assert [x.id for x in log] == [e.id] and log[0].file == str(target) and log[0].params["map_id"] == "JU02_c300"
    # service.run of an export op is routed to the log too (MCP run_operation, old callers)
    service.run(loaded, "export.map", {"map_id": "JU02_c300", "layer": {"kind": "phase"}})
    assert len(loaded.recipe().steps) == n and len(exportlog.load(loaded.root)) == 2
    # export again with the same settings, to the same file
    target.unlink()
    _s, e2 = service.export_again(loaded, e.id)
    assert target.is_file() and e2.params == e.params and e2.id != e.id
    assert "JU02_c300 ipf" in e.label()


def test_migrate_moves_export_steps_to_log(project, tmp_path):
    from ti64app import exportlog
    from ti64app.project import Project
    from ti64app.recipe import Recipe, RecipeStep

    r = Recipe(steps=[RecipeStep(id="a1", op="ebsd.load", params={"map_id": "M"}),
                      RecipeStep(id="e1", op="export.map", params={"map_id": "M", "layer": {"kind": "ipf"}},
                                 backend="pymtex"),
                      RecipeStep(id="e2", op="sem.export", params={"stitch": "s", "detector": "BSE"})])
    r.save(project.recipe_path)
    snap = project.root / ".history" / "undo" / "x1"
    snap.mkdir(parents=True)
    r.save(snap / "recipe.json")
    p = Project.open(project.root)
    assert [s.op for s in p.recipe().steps] == ["ebsd.load"]
    log = exportlog.load(p.root)
    assert [e.id for e in log] == ["e1", "e2"] and all(e.migrated for e in log)
    assert [s["op"] for s in json.loads((snap / "recipe.json").read_text())["steps"]] == ["ebsd.load"]
    Project.open(project.root)                                  # idempotent
    assert len(exportlog.load(p.root)) == 2


# ---- results: save / export a figure (fix 3.14) ---------------------------------------------------------
def test_figure_save_and_export(loaded, tmp_path):
    from ti64app import exportlog, service

    st = service.run(loaded, "figure", {"map_id": "JU02_c300", "kind": "phase"})
    svg = service.save_figure(loaded, st.id, "svg", tmp_path / "f.svg")
    assert svg.read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg"))
    pdf = service.save_figure(loaded, st.id, "pdf", tmp_path / "f.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"
    with pytest.raises(ValueError, match="MATLAB"):
        service.save_figure(loaded, st.id, "fig", tmp_path / "f.fig")
    e = service.export_result(loaded, st.id, "jpg", tmp_path / "f.jpg")
    assert (tmp_path / "f.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert exportlog.load(loaded.root)[-1].op == "result.export" and e.params["format"] == "jpg"
    (tmp_path / "f.jpg").unlink()
    service.export_again(loaded, e.id)
    assert (tmp_path / "f.jpg").is_file()


# ---- delete a step with dependents (fix 3.13) -------------------------------------------------------------
def test_delete_step_mark_dependents_stale(loaded):
    from ti64app import service

    g = service.run(loaded, "grains.calc", {"map_id": "JU02_c300"})
    f = service.run(loaded, "figure", {"map_id": "JU02_c300", "kind": "ipf", "overlays": ["hagb"]})
    plan = service.plan_delete(loaded, [g.id])
    assert f.id in plan["dependents"]
    res = service.delete_step(loaded, g.id, dependents="stale")
    ids = {s.id: s for s in loaded.recipe().steps}
    assert g.id not in ids and ids[f.id].stale and res["stale"] == [f.id]
    assert service.undo(loaded)
    ids = {s.id: s for s in loaded.recipe().steps}
    assert g.id in ids and not ids[f.id].stale
    service.delete_step(loaded, g.id, dependents="delete")
    ids = {s.id for s in loaded.recipe().steps}
    assert g.id not in ids and f.id not in ids


# ---- feedback (fix 3.2), memory (3.1), Pictures (3.3) ------------------------------------------------------
def test_feedback_report_paths_and_urls(tmp_path):
    from ti64app import feedback as fb

    assert fb.shorten_paths(r'File "C:\Users\me\proj\x\ebsd_tab.py", line 3') == 'File "ebsd_tab.py", line 3'
    assert fb.shorten_paths("/home/u/a/b.txt ok") == "b.txt ok"
    assert fb.shorten_paths("at 12:30:01 http://127.0.0.1:8080/x") == "at 12:30:01 http://127.0.0.1:8080/x"
    lines = [rf"2026 ERROR loading C:\Users\me\data\JU02_{i}.ang" for i in range(100)]
    r = fb.build_report("Crash when loading\nsteps...", "bug", "pymtex", lines,
                        {"os": "Windows 11", "python": "3.12", "ram": "16 GB"})
    assert r["title"] == "[Bug] Crash when loading" and "Users" not in r["body"] and "JU02_99.ang" in r["body"]
    assert "backend: pymtex" in r["body"] and "app version" in r["body"]
    for cat, subj in (("bug", "[Ti64] Bug"), ("idea", "[Ti64] Idea"), ("question", "[Ti64] Question")):
        assert fb.subject(cat) == subj
        u = fb.mailto_url("vondrm34@cvut.cz", cat, r, "x.zip")
        assert u.startswith("mailto:vondrm34@cvut.cz?subject=" + subj.replace("[", "%5B").replace("]", "%5D")
                            .replace(" ", "%20"))
        assert len(u) <= fb.MAX_URL
    g = fb.github_issue_url("https://github.com/o/r.git", r)
    assert g.startswith("https://github.com/o/r/issues/new?title=") and len(g) <= fb.MAX_URL
    z = fb.write_zip(tmp_path, r, b"\x89PNG fake")
    names = zipfile.ZipFile(z).namelist()
    assert set(names) == {"report.md", "log_tail.txt", "screenshot.png"}
    z2 = fb.write_zip(tmp_path / "b", r, None)
    assert "screenshot.png" not in zipfile.ZipFile(z2).namelist()


def test_feedback_settings_defaults_and_save():
    from ti64app.settings import load_settings, save_preferences

    s = load_settings()
    assert s.feedback.email == "vondrm34@cvut.cz" and "github.com" in s.feedback.repo_url
    s.feedback.email = "x@y.cz"
    save_preferences(s)
    assert load_settings().feedback.email == "x@y.cz"


def test_memory_status_and_pictures():
    from ti64app.settings import pictures_dir
    from ti64app.sysinfo import memory_status, memory_text

    m = memory_status()
    assert m["python"] > 0 and m["pc_total"] >= m["pc_used"] > 0 and m["app"] >= m["python"]
    short, tip = memory_text(m)
    assert short.startswith("App ") and "PC" in short and "80 %" in tip
    assert pictures_dir().is_dir()


# ---- prior-beta grains (fix 3.10) ---------------------------------------------------------------------------
def test_prior_beta_grains(loaded):
    from ti64app import service
    from ti64app.ops_ebsd import grains_target
    from ti64app.recipe import references

    ga = service.run(loaded, "grains.calc", {"map_id": "JU02_c300"})
    r = service.run(loaded, "recon.run", {"map_id": "JU02_c300"})
    gb = service.run(loaded, "grains.calc", {"map_id": "JU02_c300", "source": r.id,
                                             "grains": {"hagb_deg": 15.0, "lagb_deg": 2.0}})
    assert grains_target(loaded, gb) == "beta" and grains_target(loaded, ga) == "alpha"
    assert r.id in references(gb, {s.id for s in loaded.recipe().steps})
    assert 0 < gb.metrics["n_grains"] < ga.metrics["n_grains"]       # parent grains are much larger
    k = service.run(loaded, "map.compute", {"map_id": "JU02_c300", "kind": "kam"})
    assert k.params["grains"] == ga.id                               # alpha steps never pick prior-beta grains


def test_hist_edges_range():
    import numpy as np

    from ti64app.modules.grain_size import hist_edges

    v = np.array([0.5, 1, 2, 3, 4, 5, 8, 20.0])
    e = hist_edges(v, bin_width=1.0, lo=1.0, hi=6.0)
    assert e[0] == 1.0 and e[-1] == 6.0 and np.allclose(np.diff(e), 1.0)
    e = hist_edges(v, lo=2.0, hi=10.0)
    assert e[0] == 2.0 and e[-1] == 10.0 and len(e) >= 6
    with pytest.raises(ValueError, match="larger than the minimum"):
        hist_edges(v, lo=5.0, hi=2.0)
    assert hist_edges(v, bin_width=1.0)[0] == 0.0                     # unchanged without a range
