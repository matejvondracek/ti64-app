"""Recipe: every GUI/MCP action is one step in projects/<name>/recipe.json.

Each step records the backend name and the versions (app, mtex/pymtex, MATLAB) so it can be replayed.
Replay uses the recorded backend unless the user overrides it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RecipeStep(BaseModel):
    op: str                                  # operation name from the backend protocol / modules
    params: dict[str, Any] = Field(default_factory=dict)
    backend: str
    versions: dict[str, str] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    stale: bool = False


class Recipe(BaseModel):
    steps: list[RecipeStep] = Field(default_factory=list)

    def add(self, step: RecipeStep) -> RecipeStep:
        self.steps.append(step)
        return step

    def mark_stale_from(self, index: int) -> None:
        for s in self.steps[index:]:
            s.stale = True

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.model_dump(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Recipe":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
