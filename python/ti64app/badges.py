"""Status badges of the EBSD tab sections (phase 6.6 fix 5, item 10).

Rule for every section: no badge when the section was never used (no applied step of that section for this map);
'changed, not applied' only when the fields differ from the last applied step of the section; 'applied <time>' when
that step is current; 'stale' when an upstream step changed after it (its source map, grains or reconstruction is no
longer the one a new step would use, or it was marked stale).
"""
from __future__ import annotations

from datetime import datetime

from .project import Project
from .recipe import MAP_CHAIN_OPS, RecipeStep, current_map_step


def _newest(steps, pred) -> RecipeStep | None:
    hits = [s for s in steps if not s.stale and pred(s)]
    return hits[-1] if hits else None


def upstream_reason(project: Project, step: RecipeStep) -> str | None:
    """Why a step is out of date (plain language), None if it is current."""
    if step.stale:
        return "it was marked stale (an earlier step or the map sheet changed)"
    steps = project.recipe().steps
    by_id = {s.id: s for s in steps}
    mid = step.params.get("map_id")
    recon_ids = {s.id for s in steps if s.op == "recon.run"}
    src = step.params.get("source")
    if src:
        s = by_id.get(src)
        if s is None or s.stale:
            return f"its source step {src} was removed or is stale"
        if step.op not in MAP_CHAIN_OPS and s.op in MAP_CHAIN_OPS:
            cur = current_map_step(steps, mid)
            if cur is not None and cur.id != src:
                return f"the map changed after it ({cur.op} {cur.id})"
        if s.op == "recon.run":
            r = _newest(steps, lambda t: t.op == "recon.run" and t.params.get("map_id") == mid)
            if r is not None and r.id != src:
                return f"a newer reconstruction exists ({r.id})"
    g = step.params.get("grains")
    if isinstance(g, str) and g in by_id:
        gs = by_id[g]
        beta = gs.params.get("source") in recon_ids
        new = _newest(steps, lambda t: t.op == "grains.calc" and t.params.get("map_id") == mid
                      and (t.params.get("source") in recon_ids) == beta)
        if gs.stale:
            return f"its grains {g} are stale"
        if new is not None and new.id != g and step.op != "grainsize.run":
            return f"newer grains exist ({new.id})"
        up = upstream_reason(project, gs)
        if up:
            return f"its grains {g} are out of date: {up}"
    r = step.params.get("recon")
    if isinstance(r, str) and r in by_id and step.op != "recon.run":
        new = _newest(steps, lambda t: t.op == "recon.run" and t.params.get("map_id") == mid)
        if by_id[r].stale or (new is not None and new.id != r):
            return f"a newer reconstruction exists ({new.id if new else '?'})"
    return None


def when(step: RecipeStep) -> str:
    """Local HH:MM (and the date if not today) of a step."""
    try:
        t = datetime.fromisoformat(step.timestamp).astimezone()
    except ValueError:
        return step.timestamp
    return t.strftime("%H:%M") if t.date() == datetime.now().date() else t.strftime("%d.%m. %H:%M")


def same(a, b) -> bool:
    """Field value equals a recorded parameter (numbers with a tolerance, '' = None)."""
    a = None if a == "" else a
    b = None if b == "" else b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


def state(project: Project, step: RecipeStep | None, fields: dict, recorded: dict) -> tuple[str, str]:
    """(badge kind, tooltip): kind '' (never used), 'changed', 'stale' or 'applied'."""
    if step is None:
        return "", ""
    diff = [k for k in recorded if k in fields and not same(fields[k], recorded[k])]
    if diff:
        return "changed", (f"Changed, not applied: {', '.join(diff)} differ from the last applied step ({step.id}, "
                           f"{when(step)}). Apply to use the new values; the map still shows the applied ones.")
    why = upstream_reason(project, step)
    if why:
        return "stale", f"Stale: {why}. Apply again to update this result."
    return "applied", f"Applied at {when(step)} (step {step.id}) and current: nothing it depends on changed since."
