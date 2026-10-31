"""Project folder projects/<name>/ (git-ignored):

  project.json      name, backend, created, app version
  recipe.json       steps (ti64app.recipe)
  samples/<map>.json  sample sheets (written by the 'sample.set' step)
  outputs/<step>/   step outputs (npz / json)
  figures/, cache/
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from . import __version__
from .recipe import Recipe
from .sample import SampleSheet
from .settings import BackendName, REPO_ROOT

PROJECTS_DIR = REPO_ROOT / "projects"


def projects_dir() -> Path:
    """Where projects live: TI64_PROJECTS_DIR if set, else <repo>/projects (git-ignored)."""
    return Path(os.environ.get("TI64_PROJECTS_DIR") or PROJECTS_DIR)


class ProjectInfo(BaseModel):
    name: str
    backend: BackendName = "pymtex"
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    app_version: str = __version__


class Project:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths --------------------------------------------------------------------------------
    @property
    def info_path(self) -> Path:
        return self.root / "project.json"

    @property
    def recipe_path(self) -> Path:
        return self.root / "recipe.json"

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def name(self) -> str:
        return self.info().name

    # -- create / open ------------------------------------------------------------------------
    @classmethod
    def create(cls, name: str, base: Path | None = None, backend: str = "pymtex") -> "Project":
        root = Path(base or projects_dir()) / name
        if root.exists():
            raise FileExistsError(root)
        p = cls(root)
        for d in (root, p.samples_dir, p.outputs_dir, p.figures_dir, p.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        p.save_info(ProjectInfo(name=name, backend=backend))
        Recipe().save(p.recipe_path)
        return p

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = Path(root)
        if not (root / "project.json").is_file():
            raise FileNotFoundError(root / "project.json")
        return cls(root)

    @classmethod
    def open_or_create(cls, name: str, base: Path | None = None) -> "Project":
        root = Path(base or projects_dir()) / name
        return cls.open(root) if (root / "project.json").is_file() else cls.create(name, base)

    # -- state --------------------------------------------------------------------------------
    def info(self) -> ProjectInfo:
        return ProjectInfo.model_validate_json(self.info_path.read_text(encoding="utf-8"))

    def save_info(self, info: ProjectInfo) -> None:
        self.info_path.write_text(info.model_dump_json(indent=1), encoding="utf-8")

    def recipe(self) -> Recipe:
        return Recipe.load(self.recipe_path)

    def save_recipe(self, recipe: Recipe) -> None:
        recipe.save(self.recipe_path)

    def sample(self, map_id: str) -> SampleSheet:
        f = self.samples_dir / f"{map_id}.json"
        if not f.is_file():
            raise KeyError(f"no sample sheet for map {map_id!r}; enter it first (GUI or MCP set_sample_sheet)")
        return SampleSheet.model_validate_json(f.read_text(encoding="utf-8"))

    def samples(self) -> list[SampleSheet]:
        return [SampleSheet.model_validate_json(f.read_text(encoding="utf-8"))
                for f in sorted(self.samples_dir.glob("*.json"))]

    def set_backend(self, backend: str) -> int:
        """Switch the project's backend; marks backend-dependent results stale. Returns how many."""
        info = self.info()
        if info.backend == backend:
            return 0
        info.backend = backend
        self.save_info(info)
        r = self.recipe()
        n = r.mark_stale(lambda s: s.backend is not None and s.backend != backend)
        self.save_recipe(r)
        return n

    def summary(self) -> dict:
        info = self.info()
        r = self.recipe()
        return {"name": info.name, "root": str(self.root), "backend": info.backend,
                "maps": [s.map_id for s in self.samples()],
                "steps": [{"id": s.id, "op": s.op, "backend": s.backend, "stale": s.stale} for s in r.steps]}


def list_projects(base: Path | None = None) -> list[str]:
    base = Path(base or projects_dir())
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "project.json").is_file())


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
