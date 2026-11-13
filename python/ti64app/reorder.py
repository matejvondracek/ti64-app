"""Reorder the analysis steps of one map (History panel, drag and drop).

A new order is checked first: every step needs certain earlier steps of the same map (cleanup needs a loaded map,
grains need a loaded or cleaned map, grain size needs grains, a reconstruction figure needs a reconstruction, ...).
An invalid order is refused with the reason. A valid one is applied by re-running the map's steps from the first
changed position in the new order: the old steps from that position on are marked stale (kept, visible as stale),
references to other steps of the map that were resolved automatically (source, grains, recon, values) are resolved
again for the new order, and the whole reorder is ONE undo entry.
"""
from __future__ import annotations

from typing import Callable

from . import history, service
from .project import Project
from .recipe import RecipeStep

MAP_OPS = ("ebsd.load", "ebsd.cleanup", "ebsd.clean")
REQUIRES: dict[str, tuple[tuple[str, ...], str]] = {
    "ebsd.cleanup": (("ebsd.load",), "a loaded map (Load map)"),
    "ebsd.clean": (("ebsd.load",), "a loaded map (Load map)"),
    "grains.calc": (MAP_OPS, "a loaded or cleaned map"),
    "map.compute": (MAP_OPS, "a loaded or cleaned map"),
    "figure": (MAP_OPS, "a loaded or cleaned map"),
    "export.map": (MAP_OPS, "a loaded or cleaned map"),
    "compare.oim": (MAP_OPS, "a loaded or cleaned map"),
    "recon.run": (MAP_OPS, "a loaded or cleaned map"),
    "recon.sweep": (MAP_OPS, "a loaded or cleaned map"),
    "recon.figure": (("recon.run",), "a prior-β reconstruction"),
    "grainsize.run": (("grains.calc",), "grains (Grains and boundaries)"),
    "grainsize.figure": (("grainsize.run",), "a grain-size analysis"),
    "grainsize.compare_oim": (("grainsize.run",), "a grain-size analysis"),
}
REF_KEYS = ("source", "grains", "recon", "values", "grainsize")
NAMES = {"ebsd.load": "Load map", "ebsd.cleanup": "Cleanup", "ebsd.clean": "Cleanup operation", "grains.calc": "Grains and boundaries",
         "map.compute": "KAM / GROD / GOS / GND map", "figure": "Figure", "compare.oim": "Compare with OIM",
         "recon.run": "Prior-β reconstruction", "recon.sweep": "Parameter sweep", "recon.figure": "Reconstruction figure",
         "grainsize.run": "Grain size", "grainsize.figure": "Grain-size figure",
         "grainsize.compare_oim": "Grain size vs OIM", "annotations.set": "Annotations"}
FIXED_OPS = ("sample.set", "ebsd.crop", "compare.backends")          # never moved by a reorder


def map_steps(project: Project, map_id: str) -> list[RecipeStep]:
    """The current (non-stale) analysis steps of a map in recipe order (sheet records excluded)."""
    return [s for s in project.recipe().steps
            if not s.stale and s.params.get("map_id") == map_id and s.op not in FIXED_OPS]


def check_order(steps: list[RecipeStep]) -> str | None:
    """None if every step has what it needs before it, else the reason (plain language)."""
    seen: list[str] = []
    for k, s in enumerate(steps):
        req = REQUIRES.get(s.op)
        if s.op == "grainsize.run" and s.params.get("target") == "prior_beta":
            req = (("recon.run",), "a prior-β reconstruction")
        if s.op == "grains.calc" and any(t.id == s.params.get("source") and t.op == "recon.run" for t in steps):
            req = (("recon.run",), "a prior-β reconstruction")            # prior-β grains (fix 3.10)
        if req and not any(o in seen for o in req[0]):
            return f"step {k + 1} ({NAMES.get(s.op, s.op)}) needs {req[1]} before it"
        if s.op == "ebsd.load" and k > 0 and any(o in REQUIRES for o in seen):
            return "Load map must come before the steps that use the map"
        seen.append(s.op)
    return None


def _unresolved(params: dict, ids: set[str]) -> dict:
    """Params with automatic references to steps of this map removed (they are resolved again on the rerun)."""
    out = dict(params)
    for k in REF_KEYS:
        if isinstance(out.get(k), str) and out[k] in ids:
            out.pop(k)
    lay = out.get("layer")
    if isinstance(lay, dict):
        out["layer"] = {k: v for k, v in lay.items() if not (k in REF_KEYS and isinstance(v, str) and v in ids)}
    return out


def reorder(project: Project, map_id: str, new_order: list[str],
            on_step: Callable[[int, int, str], None] | None = None) -> dict:
    """Apply a new order (step ids of map_steps). Returns {first_changed, rerun: [new ids], stale: [old ids]}.
    Raises ValueError with the reason when the order is invalid."""
    steps = map_steps(project, map_id)
    by_id = {s.id: s for s in steps}
    if sorted(new_order) != sorted(by_id):
        raise ValueError("the new order must contain exactly the current steps of this map")
    new = [by_id[i] for i in new_order]
    reason = check_order(new)
    if reason:
        raise ValueError(f"This order is not possible: {reason}.")
    first = next((k for k, (a, b) in enumerate(zip(steps, new)) if a.id != b.id), None)
    if first is None:
        return {"first_changed": None, "rerun": [], "stale": []}
    old_ids = [s.id for s in steps[first:]]
    ids = set(by_id)
    rerun: list[str] = []
    with history.group(project, f"reorder {map_id}: rerun from step {first + 1}"):
        r = project.recipe()
        r.mark_stale(lambda s: s.id in old_ids)
        project.save_recipe(r)
        todo = new[first:]
        for k, s in enumerate(todo):
            if on_step:
                on_step(k, len(todo), s.op)
            step = service.run(project, s.op, _unresolved(s.params, ids), backend=s.backend)
            rerun.append(step.id)
    return {"first_changed": first, "rerun": rerun, "stale": old_ids}
