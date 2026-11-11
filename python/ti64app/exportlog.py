"""Export log (CLAUDE.md: exports are not recipe steps).

Exports (export.map, sem.export) run through service.export(): the op runs without being recorded, its files go to
exports/<id>/ in the project and one entry is appended to export_log.json:

  {id, op, params, backend, versions, date, file (where the user saved it, "" if not copied), project_file,
   sidecar, sample_id, map_id, seconds}

"Export again with the same settings" re-runs the entry's op with its params (service.export_again). The log is not
undoable (not a state file) and survives Clear cache. Projects from before fix 3 had export steps in the recipe:
migrate_recipe() moves them here (also in the undo snapshots) when the project is opened.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

EXPORT_OPS = ("export.map", "sem.export")
LOG_NAME = "export_log.json"


class ExportEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    backend: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    date: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    file: str = ""                     # the file the user saved (absolute); "" when it was only kept in the project
    project_file: str = ""             # relative to the project root
    sidecar: str = ""                  # relative to the project root
    sample_id: str | None = None
    map_id: str | None = None
    seconds: float = 0.0
    migrated: bool = False             # moved from an old recipe (fix 3.4)

    def label(self) -> str:
        prm = self.params
        if self.op == "result.export":
            return f"{prm.get('map_id', '')} {prm.get('figure_op', 'figure')} {prm.get('kind', '')} · "                    f"{str(prm.get('format', 'png')).upper()}".replace("  ", " ").strip()
        if self.op == "export.map":
            what = f"{prm.get('map_id', '')} {(prm.get('layer') or {}).get('kind', 'map')}"
        else:
            what = f"{prm.get('detector', '')} " + ("mosaic" if prm.get("stitch") else Path(prm.get("image") or
                                                                                          "image").name)
        mode = prm.get("mode", "native")
        res = {"native": "native", "scale": f"×{prm.get('scale')}", "width": f"{prm.get('width_mm')} mm @ "
                                                                            f"{prm.get('dpi')} dpi"}.get(mode, mode)
        return f"{what.strip()} · {str(prm.get('format', 'png')).upper()} · {res}"


def log_path(root: Path) -> Path:
    return Path(root) / LOG_NAME


def load(root: Path) -> list[ExportEntry]:
    f = log_path(root)
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [ExportEntry.model_validate(e) for e in data.get("exports", [])]


def save(root: Path, entries: list[ExportEntry]) -> None:
    from .project import write_json  # noqa: PLC0415

    write_json(log_path(root), {"exports": [e.model_dump(mode="json") for e in entries]})


def append(root: Path, entry: ExportEntry) -> ExportEntry:
    save(root, load(root) + [entry])
    return entry


def get(root: Path, entry_id: str) -> ExportEntry:
    for e in load(root):
        if e.id == entry_id:
            return e
    raise KeyError(f"no export {entry_id!r} in the export log")


def remove(root: Path, entry_id: str) -> None:
    save(root, [e for e in load(root) if e.id != entry_id])


def _entry_of_step(step: dict) -> ExportEntry:
    out = step.get("outputs") or {}
    prm = step.get("params") or {}
    ts = step.get("timestamp") or ""
    try:                                       # recipe timestamps are UTC; the log shows local time
        ts = datetime.fromisoformat(ts).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        pass
    return ExportEntry(id=step.get("id") or uuid.uuid4().hex[:8], op=step["op"], params=prm,
                       backend=step.get("backend"), versions=step.get("versions") or {}, date=ts,
                       project_file=(out.get("export") or {}).get("path", ""),
                       sidecar=(out.get("sidecar") or {}).get("path", ""), sample_id=step.get("sample_id"),
                       map_id=prm.get("map_id"), seconds=float((step.get("metrics") or {}).get("seconds", 0.0)),
                       migrated=True)


def split_recipe(data: dict) -> tuple[dict, list[dict]]:
    """(recipe without export steps, the export steps)."""
    steps = data.get("steps") or []
    keep = [s for s in steps if s.get("op") not in EXPORT_OPS]
    return {**data, "steps": keep}, [s for s in steps if s.get("op") in EXPORT_OPS]


def migrate_recipe(root: Path) -> int:
    """Move export steps of recipe.json (and of the undo snapshots) to the export log. Returns how many moved."""
    from .project import write_json  # noqa: PLC0415

    root = Path(root)
    f = root / "recipe.json"
    n = 0
    if f.is_file():
        data = json.loads(f.read_text(encoding="utf-8"))
        rest, exp = split_recipe(data)
        if exp:
            have = {e.id for e in load(root)}
            save(root, load(root) + [_entry_of_step(s) for s in exp if s.get("id") not in have])
            write_json(f, rest)
            n = len(exp)
    undo = root / ".history" / "undo"
    if undo.is_dir():                          # undo must not bring export steps back into the recipe
        for snap in undo.glob("*/recipe.json"):
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rest, exp = split_recipe(data)
            if exp:
                write_json(snap, rest)
    return n
