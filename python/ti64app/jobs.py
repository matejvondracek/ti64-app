"""Job runner: runs recipe steps on the chosen backend and records them. (Skeleton.)"""
from __future__ import annotations

from typing import Any

from .backends import get_backend
from .recipe import RecipeStep


def run_step(op: str, params: dict[str, Any], backend: str = "pymtex") -> RecipeStep:
    """Run one operation and return the recipe step that records it. Not implemented yet."""
    be = get_backend(backend)
    raise NotImplementedError(f"operation {op!r} is not implemented yet ({be.name} backend)")
