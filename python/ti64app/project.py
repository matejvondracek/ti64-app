"""Project folder: projects/<name>/ (git-ignored) with sample.json, recipe.json, cache/, figures/."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .recipe import Recipe
from .sample import SampleSheet
from .settings import REPO_ROOT

PROJECTS_DIR = REPO_ROOT / "projects"


@dataclass
class Project:
    root: Path

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def recipe_path(self) -> Path:
        return self.root / "recipe.json"

    @property
    def sample_path(self) -> Path:
        return self.root / "sample.json"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @classmethod
    def create(cls, name: str, sample: SampleSheet, base: Path = PROJECTS_DIR) -> "Project":
        root = base / name
        if root.exists():
            raise FileExistsError(root)
        for d in (root, root / "cache", root / "figures"):
            d.mkdir(parents=True)
        p = cls(root)
        p.sample_path.write_text(sample.model_dump_json(indent=1), encoding="utf-8")
        Recipe().save(p.recipe_path)
        return p

    @classmethod
    def open(cls, root: Path) -> "Project":
        if not (root / "recipe.json").is_file():
            raise FileNotFoundError(root / "recipe.json")
        return cls(root)

    def sample(self) -> SampleSheet:
        return SampleSheet.model_validate_json(self.sample_path.read_text(encoding="utf-8"))

    def recipe(self) -> Recipe:
        return Recipe.load(self.recipe_path)
