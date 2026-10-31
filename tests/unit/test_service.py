"""Recipe steps and replay on the 300x300 crops (pymtex backend)."""
import json
import shutil

import numpy as np
import pytest

from ti64app import service
from ti64app.backends.base import BackendUnavailable
from tests.conftest import sample_params


def test_sample_set_records_step(project, crop300_ju02):
    step = service.run(project, "sample.set", sample_params(crop300_ju02))
    assert step.backend is None and step.versions["app"]
    assert step.inputs[0].path == str(crop300_ju02) and step.inputs[0].fingerprint.startswith("edge-sha256:")
    assert project.sample("JU02_c300").build_direction.unit_vector == (0.0, -1.0, 0.0)
    assert (project.root / step.outputs["sample"].path).is_file()


def test_sample_set_rejects_missing_file(project, tmp_path):
    with pytest.raises(FileNotFoundError):
        service.run(project, "sample.set", sample_params(tmp_path / "nope.ang"))
    assert project.recipe().steps == []
    assert not any(project.outputs_dir.iterdir())          # failed step leaves no output folder


def test_load_requires_sample_sheet(project):
    with pytest.raises(KeyError, match="sample sheet"):
        service.run(project, "ebsd.load", {"map_id": "JU02_c300"})


def test_load_and_replay_reproduce(project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    step = service.run(project, "ebsd.load", {"map_id": "JU02_c300"})
    assert step.backend == "pymtex" and step.versions["mtex"] == "1.0.0"
    assert step.metrics["n_points"] == 90000 and step.metrics["load_s"] > 0
    meta = json.loads((project.root / step.outputs["meta"].path).read_text())
    assert meta["input_state"] == "oim_cleaned" and meta["grid"] == "hex"
    with np.load(project.root / step.outputs["ebsd"].path) as z:
        assert z["quat"].shape == (90000, 4)
    assert (str(project.root), step.id) in service.SESSION      # object kept in the backend
    rep = service.replay(project)
    assert rep.ok, rep
    assert {c.status for c in rep.checks} == {"identical"}
    assert service.verify_outputs(project) == []


def test_resetting_sample_marks_map_steps_stale(project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    service.run(project, "ebsd.load", {"map_id": "JU02_c300"})
    p2 = sample_params(crop300_ju02)
    p2["input_state"] = "raw"
    service.run(project, "sample.set", p2)
    assert [s.stale for s in project.recipe().steps] == [True, True, False]
    # replay skips stale steps by default
    assert [c.op for c in service.replay(project).checks] == ["sample.set"]


def test_replay_detects_changed_input(project, crop300_ju02, tmp_path):
    f = tmp_path / crop300_ju02.name
    shutil.copy(crop300_ju02, f)
    service.run(project, "sample.set", sample_params(f))
    service.run(project, "ebsd.load", {"map_id": "JU02_c300"})
    with open(f, "r+b") as fh:                 # change one CI value in the last data row
        fh.seek(-30, 2)
        b = fh.read(30)
        fh.seek(-30, 2)
        fh.write(b.replace(b"0.", b"1.", 1))
    rep = service.replay(project)
    assert not rep.ok and rep.input_changes


def test_verify_outputs_detects_edit(project, crop300_ju02):
    step = service.run(project, "sample.set", sample_params(crop300_ju02))
    out = project.root / step.outputs["sample"].path
    d = json.loads(out.read_text())
    d["specimen_id"] = "changed"
    out.write_text(json.dumps(d))
    assert service.verify_outputs(project) == [f"{step.id}:sample changed"]


def test_unavailable_backend_is_reported(project, crop300_ju02, tmp_path, monkeypatch):
    f = tmp_path / "s.toml"
    f.write_text("[paths]\nmtex_path = ''\n", encoding="utf-8")
    monkeypatch.setenv("TI64_SETTINGS", str(f))
    service.run(project, "sample.set", sample_params(crop300_ju02))
    with pytest.raises(BackendUnavailable, match="matlab backend unavailable"):
        service.run(project, "ebsd.load", {"map_id": "JU02_c300"}, backend="matlab")


def test_up2_header_op(project, up2_file):
    step = service.run(project, "up2.header", {"file": str(up2_file)})
    h = json.loads((project.root / step.outputs["header"].path).read_text())
    assert h["n_patterns"] == 6_005_001 and h["size_ok"]


def test_unknown_op(project):
    with pytest.raises(KeyError):
        service.run(project, "nope", {})
