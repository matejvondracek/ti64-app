"""Undo / redo of project actions (GUI and MCP).

Every recorded action (service.run with record=True, a backend switch) is bracketed by snapshots of the
project's small STATE files: project.json, recipe.json, samples.json, files.json, annotations.json. Undo restores the
state before the last action, redo the state after it. Step outputs (outputs/<step>/, maps/*.ang) are not
copied: they stay on disk while an undone step can still be redone, and are deleted when a new action
discards the redo stack. The stack lives in .history/undo/ (at most MAX_UNDO actions) and survives closing the
app and "Clear cache".
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .project import Project

MAX_UNDO = 50


def _dir(project: Project) -> Path:
    return project.history_dir / "undo"


def _state_files(project: Project) -> list[Path]:
    from .project import STATE_FILES  # noqa: PLC0415

    return [project.root / f for f in STATE_FILES if (project.root / f).is_file()]


_GROUP = threading.local()


@contextmanager
def group(project: Project, label: str):
    """Several recorded actions as ONE undo entry (e.g. a reorder that re-runs steps): inner snapshots / commits are
    skipped; the entry is committed at the end, also when an inner action fails (the state may have changed)."""
    before = snapshot(project)
    _GROUP.depth = getattr(_GROUP, "depth", 0) + 1
    try:
        yield
    finally:
        _GROUP.depth -= 1
        commit(project, label, before)


def _grouped() -> bool:
    return getattr(_GROUP, "depth", 0) > 0


def snapshot(project: Project) -> str:
    """Copy the state files; returns the snapshot id ("" inside a group)."""
    if _grouped():
        return ""
    sid = uuid.uuid4().hex[:10]
    d = _dir(project) / sid
    d.mkdir(parents=True)
    for f in _state_files(project):
        rel = f.relative_to(project.root)
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, d / rel)
    return sid


def _restore(project: Project, sid: str) -> None:
    d = _dir(project) / sid
    if project.recipe_path.is_file():
        from .recipe import _keep_version  # noqa: PLC0415

        _keep_version(project.recipe_path)
    for f in _state_files(project):                       # files that did not exist in the snapshot go
        if not (d / f.relative_to(project.root)).is_file():
            f.unlink()
    for f in d.rglob("*"):
        if f.is_file():
            dst = project.root / f.relative_to(d)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)


def _load(project: Project) -> dict:
    f = _dir(project) / "stack.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"undo": [], "redo": []}


def _save(project: Project, st: dict) -> None:
    from .project import write_json  # noqa: PLC0415

    write_json(_dir(project) / "stack.json", st)


def _step_ids(project: Project, sid: str) -> set[str]:
    f = _dir(project) / sid / "recipe.json"
    if not f.is_file():
        return set()
    return {s["id"] for s in json.loads(f.read_text(encoding="utf-8")).get("steps", [])}


def _drop(project: Project, entries: list[dict]) -> None:
    """Delete snapshots and the outputs of steps that only they still reference."""
    current = {s.id for s in project.recipe().steps}
    keep: set[str] = set()
    for e in _load(project)["undo"]:
        keep |= _step_ids(project, e["before"]) | _step_ids(project, e["after"])
    for e in entries:
        for sid in (e["before"], e["after"]):
            for step in _step_ids(project, sid) - current - keep:
                d = project.find_step_dir(step)
                if d is not None:
                    shutil.rmtree(d, ignore_errors=True)
    for e in entries:
        for sid in (e["before"], e["after"]):
            if not any(sid in (u["before"], u["after"]) for u in _load(project)["undo"]):
                shutil.rmtree(_dir(project) / sid, ignore_errors=True)


def commit(project: Project, label: str, before: str) -> None:
    """Record an action whose state before was `before` (a snapshot id); clears the redo stack."""
    if _grouped() or not before:
        return
    after = snapshot(project)
    st = _load(project)
    redo, st["redo"] = st["redo"], []
    st["undo"].append({"label": label, "before": before, "after": after})
    old = st["undo"][:-MAX_UNDO] if len(st["undo"]) > MAX_UNDO else []
    st["undo"] = st["undo"][-MAX_UNDO:]
    _save(project, st)
    _drop(project, redo)
    for e in old:                                         # forget the oldest actions (their outputs stay)
        for sid in (e["before"], e["after"]):
            if not any(sid in (u["before"], u["after"]) for u in st["undo"]):
                shutil.rmtree(_dir(project) / sid, ignore_errors=True)


def discard(project: Project, before: str) -> None:
    if before:
        shutil.rmtree(_dir(project) / before, ignore_errors=True)


def undo(project: Project) -> str | None:
    """Undo the last action; returns its label (None if there is nothing to undo)."""
    st = _load(project)
    if not st["undo"]:
        return None
    e = st["undo"].pop()
    _restore(project, e["before"])
    st["redo"].append(e)
    _save(project, st)
    return e["label"]


def redo(project: Project) -> str | None:
    st = _load(project)
    if not st["redo"]:
        return None
    e = st["redo"].pop()
    _restore(project, e["after"])
    st["undo"].append(e)
    _save(project, st)
    return e["label"]


def status(project: Project) -> dict:
    st = _load(project)
    return {"undo": [e["label"] for e in st["undo"]], "redo": [e["label"] for e in st["redo"]]}
