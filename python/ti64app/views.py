"""View figures (fix 6.7.1 item 6): drawing a figure FROM a result (grain-size histograms with bin width / range /
log-normal fit, the colony map) is display, not analysis, so it is not a recipe step.

Like exports (exportlog.py), a view figure is drawn by the backend that computed the result, without a recipe step:
files go to views/<id>/ in the project, and views.json keeps
  settings   {map_id: {op: params}}   the figure settings per map (view state; the GUI fills its fields from them)
  figures    {id: {op, params, backend, versions, outputs, date}}   the drawn figures, so Save / Export / Export
             again find them like recipe steps
Old recipes with grainsize.figure steps keep working: migrate_recipe() moves those steps (also out of the undo
snapshots) into views.json when the project is opened, so they leave the History.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

VIEW_OPS = ("grainsize.figure",)
FILE = "views.json"
SETTING_SKIP = ("map_id", "grainsize")          # per-map settings: what the user chose, not which step was drawn


def _path(root: Path) -> Path:
    return Path(root) / FILE


def load(root: Path) -> dict:
    f = _path(root)
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("settings", {})
    d.setdefault("figures", {})
    return d


def save(root: Path, data: dict) -> None:
    from .project import write_json  # noqa: PLC0415  (atomic write)

    write_json(_path(root), data)


def remember(root: Path, step, settings: bool = True) -> None:
    """Record a drawn view figure (an unrecorded RecipeStep) and, unless settings=False, its settings for the map."""
    d = load(root)
    d["figures"][step.id] = {"op": step.op, "params": step.params, "backend": step.backend,
                             "versions": step.versions, "sample_id": step.sample_id,
                             "outputs": {k: v.model_dump(mode="json") for k, v in step.outputs.items()},
                             "date": datetime.now().isoformat(timespec="seconds")}
    mid = step.params.get("map_id")
    if settings and mid:
        d["settings"].setdefault(mid, {})[step.op] = {k: v for k, v in step.params.items() if k not in SETTING_SKIP}
    save(root, d)


def settings(root: Path, map_id: str, op: str = "grainsize.figure") -> dict[str, Any]:
    """The figure settings last used for a map (empty: defaults)."""
    return dict(load(root)["settings"].get(map_id, {}).get(op, {}))


def set_settings(root: Path, map_id: str, op: str, params: dict) -> None:
    d = load(root)
    d["settings"].setdefault(map_id, {})[op] = {k: v for k, v in params.items() if k not in SETTING_SKIP}
    save(root, d)


def step(root: Path, fid: str):
    """The drawn view figure `fid` as a RecipeStep (not in the recipe), or None."""
    from .recipe import RecipeStep  # noqa: PLC0415

    e = load(root)["figures"].get(fid)
    if e is None:
        return None
    return RecipeStep(id=fid, op=e["op"], params=e["params"], backend=e.get("backend"), versions=e.get("versions") or {},
                      sample_id=e.get("sample_id"), outputs=e.get("outputs") or {})


def migrate_recipe(root: Path) -> int:
    """Move view-figure steps of recipe.json (and of the undo snapshots) to views.json. Returns how many moved."""
    from .project import write_json  # noqa: PLC0415

    root = Path(root)
    n = 0
    f = root / "recipe.json"
    if f.is_file():
        data = json.loads(f.read_text(encoding="utf-8"))
        steps = data.get("steps") or []
        moved = [s for s in steps if s.get("op") in VIEW_OPS]
        if moved:
            d = load(root)
            for s in moved:
                d["figures"].setdefault(s["id"], {"op": s["op"], "params": s.get("params") or {},
                                                  "backend": s.get("backend"), "versions": s.get("versions") or {},
                                                  "sample_id": s.get("sample_id"), "outputs": s.get("outputs") or {},
                                                  "date": s.get("timestamp", ""), "migrated": True})
                mid = (s.get("params") or {}).get("map_id")
                if mid:
                    d["settings"].setdefault(mid, {})[s["op"]] = {
                        k: v for k, v in (s.get("params") or {}).items() if k not in SETTING_SKIP}
            save(root, d)
            write_json(f, {**data, "steps": [s for s in steps if s.get("op") not in VIEW_OPS]})
            n = len(moved)
    undo = root / ".history" / "undo"
    if undo.is_dir():                          # undo must not bring view steps back into the recipe
        for snap in undo.glob("*/recipe.json"):
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            steps = data.get("steps") or []
            if any(s.get("op") in VIEW_OPS for s in steps):
                write_json(snap, {**data, "steps": [s for s in steps if s.get("op") not in VIEW_OPS]})
    return n
