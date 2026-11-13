"""Recipe: every GUI/MCP action is one step in projects/<name>/recipe.json.

Step = {id, op, params, inputs, outputs, timestamp, backend, versions, metrics, stale}.
- inputs:  external files (path + fingerprint) and outputs of earlier steps ("<step_id>:<name>").
- outputs: files inside the project with a CONTENT hash (array/JSON content, not file bytes), so a
           replay can check that it reproduces them.
- metrics: run time, peak RAM, ... (informational, not compared on replay).
Replay (ti64app.service.replay) uses the recorded backend unless the user overrides it.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FileInput(BaseModel):
    path: str
    fingerprint: str                 # see hashing.file_fingerprint


class OutputRef(BaseModel):
    path: str                        # relative to the project root
    kind: Literal["npz", "json", "png", "csv", "xlsx", "tiff", "svg", "pdf", "mosaic", "file", "other"]
    content_hash: str


class RecipeStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[FileInput | str] = Field(default_factory=list)   # str = "<step_id>:<output name>"
    outputs: dict[str, OutputRef] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)
    backend: str | None = None       # None for backend-independent steps (sample sheet, up2 header, BSE)
    sample_id: str | None = None     # sample (specimen) the step belongs to (phase 6.6)
    versions: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    stale: bool = False


def references(step: RecipeStep, known: set[str]) -> set[str]:
    """Ids of the earlier steps a step uses: '<id>:<output>' inputs and parameter values (any depth) that are step
    ids (source, grains, recon, values, grainsize, stitch, ...)."""
    out = {i.split(":", 1)[0] for i in step.inputs if isinstance(i, str)}

    def walk(v):
        if isinstance(v, str):
            if v in known:
                out.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(step.params)
    out.discard(step.id)
    return out & known


class Recipe(BaseModel):
    steps: list[RecipeStep] = Field(default_factory=list)

    def add(self, step: RecipeStep) -> RecipeStep:
        self.steps.append(step)
        return step

    def step(self, step_id: str) -> RecipeStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def mark_stale(self, predicate=lambda s: True) -> int:
        n = 0
        for s in self.steps:
            if predicate(s) and not s.stale:
                s.stale = True
                n += 1
        return n

    def save(self, path: Path) -> None:
        """Atomic write; a project's previous recipe.json is kept in .history/recipe/ (last KEEP_VERSIONS)."""
        path = Path(path)
        if path.name == "recipe.json" and path.is_file() and (path.parent / "project.json").is_file():
            _keep_version(path)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Recipe":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


KEEP_VERSIONS = 20


def _keep_version(path: Path) -> None:
    d = path.parent / ".history" / "recipe"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    shutil.copy2(path, d / f"recipe_{stamp}.json")
    for old in sorted(d.glob("recipe_*.json"))[:-KEEP_VERSIONS]:
        old.unlink(missing_ok=True)


def recipe_versions(project_root: Path) -> list[Path]:
    """Saved versions of a project's recipe.json, newest first (for recovery)."""
    return sorted((Path(project_root) / ".history" / "recipe").glob("recipe_*.json"), reverse=True)


# ---- the map chain (phase 6.6 fix 5) ------------------------------------------------------------------------------
# Steps that produce a map: load, the old one-step cleanup (kept for existing recipes) and the single cleanup
# operations (ebsd.clean). Each cleanup works on the output of the step before it; the CURRENT map of a map id is
# the newest non-stale of these steps in recipe order (a 'Load again' after cleanups starts again from the loaded map).
MAP_CHAIN_OPS = ("ebsd.load", "ebsd.cleanup", "ebsd.clean")


def current_map_step(steps: list[RecipeStep], map_id: str) -> RecipeStep | None:
    hits = [s for s in steps if not s.stale and s.op in MAP_CHAIN_OPS and s.params.get("map_id") == map_id]
    return hits[-1] if hits else None
