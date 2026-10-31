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
    kind: Literal["npz", "json", "png", "other"]
    content_hash: str


class RecipeStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[FileInput | str] = Field(default_factory=list)   # str = "<step_id>:<output name>"
    outputs: dict[str, OutputRef] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)
    backend: str | None = None       # None for backend-independent steps (sample sheet, up2 header, BSE)
    versions: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    stale: bool = False


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
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Recipe":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
