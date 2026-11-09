"""Phase 6.6 project model: SampleInfo / MapInfo with inheritance, format 1 -> 2 migration, linked files (status,
relocate), sheets recorded in the recipe, recipe versions, interrupted steps, save copy, clear cache."""
import json
import os
import shutil

import pytest

from tests.conftest import sample_params
from ti64app import history, service
from ti64app.project import Project, migrate_dir, write_json
from ti64app.recipe import KEEP_VERSIONS, recipe_versions
from ti64app.sample import (FourPointBending, MapInfo, MapPosition, SampleInfo, SampleSheet, effective_sheet,
                            file_kind, suggest_role, suggest_sample_id)


def test_suggestions():
    assert suggest_sample_id("x/9445_MVH1_JU04_SED_1.bmp") == "JU04"
    assert suggest_sample_id("EBM_Ti64_JU02_C_Area 3_OIM Map 1.ang") == "JU02"
    assert suggest_sample_id("C:/data/RM02/scan_1.ang") == "RM02"
    assert suggest_sample_id("C:/data/misc/scan_1.ang") is None
    s = SampleInfo(id="JU04", bending=FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=5,
                                                      thickness_mm=3))
    m = MapInfo(id="a", file="a.ang", position=MapPosition(origin_Y_mm=0.1))
    assert suggest_role(m, s)[0] == "middle"                        # |Y| <= 10 % of t/2 = 0.15 mm
    m.position.origin_Y_mm = 1.2
    assert suggest_role(m, s)[0] is None                             # tension side unknown: never guessed
    s.tension_side = "-Y"
    assert suggest_role(m, s)[0] == "compression"
    m.position.origin_Y_mm = -1.2
    assert suggest_role(m, s)[0] == "tension"


def test_inheritance_and_overrides(tmp_path, crop300_ju02):
    p = Project.create("p", base=tmp_path)
    m = p.add_files([crop300_ju02])[0]
    sh0 = p.sample(m.id)                         # fix 2: loads without sample / build direction (never guessed)
    assert sh0.specimen_id is None and sh0.build_direction is None and sh0.input_state == "oim_cleaned"
    p.save_sample_info(SampleInfo(id="JU02", heat_treatment="HIP",
                                  bending=FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=5,
                                                           thickness_mm=3, load_N=100)))
    p.assign([m.id], "JU02")
    p.update_file(m.id, input_state="oim_cleaned", build_direction={"angle_deg": 270})
    sh = p.sample(m.id)
    assert sh.specimen_id == "JU02" and sh.bending.load_N == 100 and sh.input_state == "oim_cleaned"
    p.update_file(m.id, bending=FourPointBending(outer_span_mm=40, inner_span_mm=20, width_mm=5, thickness_mm=3,
                                                 load_N=250).model_dump())
    assert p.sample(m.id).bending.load_N == 250                      # file override
    p.update_file(m.id, bending=None)
    assert p.sample(m.id).bending.load_N == 100                      # back to the sample's value
    assert file_kind(crop300_ju02) == "ang"


def test_sheet_change_marks_results_stale_and_is_recorded(tmp_path, crop300_ju02):
    p = Project.create("p", base=tmp_path)
    m = p.add_files([crop300_ju02])[0]
    p.save_sample_info(SampleInfo(id="JU02"))
    p.assign([m.id], "JU02")
    p.update_file(m.id, input_state="oim_cleaned", build_direction={"angle_deg": 270})
    service.run(p, "ebsd.load", {"map_id": m.id})                    # the sheet is recorded first
    assert [s.op for s in p.recipe().steps] == ["sample.set", "ebsd.load"]
    assert all(s.sample_id == "JU02" for s in p.recipe().steps)
    p.update_file(m.id, notes="looked at it")                        # notes: not stale
    assert not any(s.stale for s in p.recipe().steps)
    service.run(p, "ebsd.load", {"map_id": m.id})                    # new sheet recorded, results not stale
    assert [s.op for s in p.recipe().steps] == ["sample.set", "ebsd.load", "sample.set", "ebsd.load"]
    assert [s.stale for s in p.recipe().steps] == [True, False, False, False]
    p.update_file(m.id, build_direction={"angle_deg": 90})           # BD: only what uses it goes stale (fix 2)
    assert not p.recipe().steps[-1].stale
    p.update_file(m.id, input_state="raw")                           # input state: everything of the map
    assert p.recipe().steps[-1].stale
    rep = service.replay(p)                                          # the recipe alone rebuilds the project
    assert rep.ok, rep


def test_migration_format1(tmp_path, crop300_ju02):
    root = tmp_path / "old"
    (root / "samples").mkdir(parents=True)
    write_json(root / "project.json", {"name": "old", "backend": "pymtex", "created": "2026-10-01T00:00:00+00:00",
                                       "app_version": "0.0.1"})
    write_json(root / "recipe.json", {"steps": []})
    a = SampleSheet(**sample_params(crop300_ju02, "A"))
    b = SampleSheet(**{**sample_params(crop300_ju02, "B", 90.0), "bending": {"outer_span_mm": 40, "inner_span_mm": 20,
                                                                              "width_mm": 5, "thickness_mm": 3}})
    for s in (a, b):
        write_json(root / "samples" / f"{s.map_id}.json", s.model_dump(mode="json"))
    snap = root / "cache" / "history" / "s1"                         # an old undo snapshot
    shutil.copytree(root / "samples", snap / "samples")
    shutil.copy(root / "project.json", snap / "project.json")
    p = Project.open(root)
    assert p.info().format_version == 2 and not (root / "samples").exists()
    assert (root / ".history" / "format1_samples" / "A.json").is_file()
    assert [s.id for s in p.sample_infos()] == ["EBMX-1"]
    assert p.sample("A") == a and p.sample("B") == b                 # effective sheets unchanged
    assert p.file("B").bending is not None and p.file("A").bending is None   # B differs from the sample: override
    assert (root / ".history" / "undo" / "s1" / "files.json").is_file()
    assert migrate_dir(root) == []                                   # idempotent


def test_files_status_relocate_remove(tmp_path, crop300_ju02):
    p = Project.create("p", base=tmp_path)
    f = tmp_path / "m.ang"
    shutil.copy(crop300_ju02, f)
    (tmp_path / "m.txt").write_text("x", encoding="utf-8")
    added = p.add_files([f, tmp_path / "m.txt", f])
    assert len(added) == 3 and added[0] == added[2] and len(p.files()) == 2      # linked once; lone .txt = other
    m = added[0]
    assert Project.file_status(m) == "ok"
    os.utime(f, (1, 1))
    assert Project.file_status(p.file(m.id)) == "changed"
    f.unlink()
    assert Project.file_status(p.file(m.id)) == "missing"
    g = tmp_path / "n.ang"
    shutil.copy(crop300_ju02, g)
    p.relocate(m.id, g)
    assert Project.file_status(p.file(m.id)) == "ok"
    p.remove_file(m.id)
    assert g.is_file() and p.file(m.id) is None                     # never deleted from disk


def test_recipe_versions_interrupted_copy_cache(tmp_path, crop300_ju02):
    p = Project.create("p", base=tmp_path)
    for i in range(KEEP_VERSIONS + 3):
        service.run(p, "sample.set", sample_params(crop300_ju02, f"M{i}"))
    assert len(recipe_versions(p.root)) == KEEP_VERSIONS
    history.undo(p)
    assert len(p.recipe().steps) == KEEP_VERSIONS + 2
    # interrupted: running.json of a dead process
    write_json(p.root / "running.json", {"op": "ebsd.load", "params": {"map_id": "M0"}, "pid": 999999,
                                         "pid_started": 0})
    assert p.interrupted()["op"] == "ebsd.load"
    p.clear_interrupted()
    assert p.interrupted() is None
    # save copy: state + outputs, not the cache / undo history
    (p.cache_dir / "junk.bin").write_bytes(b"0" * 1000)
    q = p.save_copy(tmp_path / "copy")
    assert q.info().name == "copy" and len(q.recipe().steps) == len(p.recipe().steps)
    assert q.files() == p.files() and not (q.cache_dir / "junk.bin").exists()
    assert not (q.root / ".history").exists()
    assert p.disk_usage()["cache"] >= 1000
    assert p.clear_cache() >= 1000 and p.disk_usage()["cache"] == 0
    assert history.status(p)["undo"]                                 # undo history survives Clear cache


def test_gui_state_atomic(tmp_path):
    p = Project.create("p", base=tmp_path)
    p.save_gui_state({"tab": "/ebsd", "drafts": {"grains": {"hagb_deg": 12}}})
    assert json.loads(p.gui_state_path.read_text(encoding="utf-8"))["drafts"]["grains"]["hagb_deg"] == 12
    assert not list(p.root.glob(".*.tmp"))
