"""compare.backends: run ONE operation on both backends and show the numbers and both figures side by side.

The two runs happen in a scratch copy of the project (cache/compare_<step>/: project.json, recipe.json, sample
sheets, annotations), so the project's own recipe keeps a single backend and its "newest step" defaults do not
change. Earlier steps the operation depends on are rebuilt on each backend from the copied recipe (materialize).
Each figure is drawn by the engine that computed the result (CLAUDE.md); the montage only places the two PNGs
next to each other without resampling. Numbers are compared, not images (conformance rule): the table lists every
numeric value of the operation's JSON outputs with the absolute and relative difference. The backend objects of
the scratch project are released afterwards (RAM < 8 GB with a full map in both engines).
"""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .backends import get_backend
from .backends.base import BackendUnavailable
from .project import Project
from .recipe import RecipeStep
from .service import OPS, OpContext, op, release_project, run

# step references an operation resolves to "the newest of the map" when not given
REF_FIELDS = ("source", "grains", "values", "recon", "grainsize")
# outputs that are not compared (huge per-pixel arrays, tables: their summaries are)
NUMBER_OUTPUT_KINDS = ("json",)
PERF_KEYS = ("seconds", "rss_GB", "peak_GB", "load_s", "transfer_s")


def default_figure(op_name: str, step: RecipeStep, kind: str | None) -> tuple[str, dict] | None:
    """Quick-look figure of a data operation: (figure op, params) referring to the step that made the data."""
    mid = step.params.get("map_id")
    if op_name in ("ebsd.load", "ebsd.cleanup"):
        return "figure", {"map_id": mid, "kind": kind or "ipf", "source": step.id}
    if op_name == "grains.calc":
        return "figure", {"map_id": mid, "kind": kind or "ipf", "source": step.params.get("source"),
                          "grains": step.id, "overlays": ["hagb"]}
    if op_name == "map.compute":
        return "figure", {"map_id": mid, "kind": kind or step.params["kind"], "values": step.id}
    if op_name == "recon.run":
        return "recon.figure", {"map_id": mid, "kind": kind or "parent_ipf", "recon": step.id}
    if op_name == "grainsize.run":
        return "grainsize.figure", {"map_id": mid, "kind": kind or "alpha_ecd", "grainsize": step.id}
    return None


def comparable_ops() -> list[str]:
    return sorted(n for n, s in OPS.items() if s.uses_backend)


def flatten_numbers(obj: Any, prefix: str = "", max_list: int = 12) -> dict[str, Any]:
    """Dotted keys -> numbers (and short strings) of a JSON value; lists longer than max_list are skipped."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten_numbers(v, f"{prefix}.{k}" if prefix else str(k), max_list))
    elif isinstance(obj, list):
        if len(obj) <= max_list:
            for i, v in enumerate(obj):
                out.update(flatten_numbers(v, f"{prefix}[{i}]", max_list))
    elif isinstance(obj, bool):
        out[prefix] = obj
    elif isinstance(obj, (int, float)):
        out[prefix] = obj
    elif isinstance(obj, str) and len(obj) <= 80:
        out[prefix] = obj
    return out


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def compare_rows(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows {key, a, b, abs_diff, rel_diff, same}; keys in first-seen order. Strings: same / different."""
    keys = list(dict.fromkeys([*a, *b]))
    rows = []
    for k in keys:
        x, y = a.get(k), b.get(k)
        fx, fy = _num(x), _num(y)
        row: dict[str, Any] = {"key": k, "a": x, "b": y, "abs_diff": None, "rel_diff": None}
        if fx is not None and fy is not None and math.isfinite(fx) and math.isfinite(fy):
            d = fy - fx
            row["abs_diff"] = d
            row["rel_diff"] = abs(d) / max(abs(fx), abs(fy)) if max(abs(fx), abs(fy)) > 0 else 0.0
            row["same"] = d == 0
        else:
            row["same"] = x == y
        rows.append(row)
    return rows


def montage(images: list[tuple[str, Path]], out: Path) -> Path:
    """Place PNGs next to each other at the same height (display only: the engines' own PNGs are kept unchanged as
    separate outputs) with a label strip above each."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    ims = [Image.open(p).convert("RGB") for _, p in images]
    h = max(im.height for im in ims)
    ims = [im if im.height == h else im.resize((round(im.width * h / im.height), h), Image.Resampling.LANCZOS)
           for im in ims]
    strip = max(28, h // 25)
    import matplotlib  # noqa: PLC0415

    # matplotlib's bundled DejaVu Sans: the same font in every process (system font lookup depends on the
    # environment, e.g. an MCP server started without WINDIR), so a replay reproduces the montage
    font = ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"),
                              int(strip * 0.6))
    gap = max(8, strip // 2)
    W = sum(im.width for im in ims) + gap * (len(ims) - 1)
    canvas = Image.new("RGB", (W, h + strip), "white")
    d = ImageDraw.Draw(canvas)
    x = 0
    for (label, _), im in zip(images, ims):
        d.text((x + 4, strip * 0.15), label, fill="black", font=font)
        canvas.paste(im, (x, strip))
        x += im.width + gap
    canvas.save(out)
    return out


def _engine_label(name: str, versions: dict[str, str]) -> str:
    if name == "matlab":
        v = versions.get("matlab", "?")
        rel = v[v.find("(") + 1:v.find(")")] if "(" in v and ")" in v else v.split(" ")[0]
        return f"matlab: MATLAB {rel} + MTEX {versions.get('mtex', '?').removeprefix('mtex-')}"
    return f"{name}: mtex (Python) {versions.get('mtex', '?')}"


def scratch_copy(project: Project, name: str) -> Project:
    """Copy of the project's state files under cache/<name>/; its step outputs are hard-linked (same volume, no copy
    of full-map arrays; outputs are never modified in place), copied only if linking fails."""
    root = project.cache_dir / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    scratch = Project(root)
    for d in (scratch.samples_dir, scratch.outputs_dir, scratch.figures_dir, scratch.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    for f in project.outputs_dir.rglob("*"):
        if f.is_file():
            dst = scratch.outputs_dir / f.relative_to(project.outputs_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(f, dst)
            except OSError:
                shutil.copy2(f, dst)
    for f in [project.info_path, project.recipe_path, project.root / "annotations.json",
              *project.samples_dir.glob("*.json")]:
        if f.is_file():
            dst = root / f.relative_to(project.root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    return scratch


class CompareBackendsParams(BaseModel):
    op: str                                   # any backend operation, e.g. recon.run, ebsd.cleanup, grains.calc
    params: dict[str, Any] = Field(default_factory=dict)   # its parameters (validated by that operation's model)
    backends: tuple[Literal["pymtex", "matlab"], Literal["pymtex", "matlab"]] = ("pymtex", "matlab")
    figure: bool = True                       # draw a figure per backend (data ops: a quick-look figure)
    figure_kind: str | None = None            # kind of that quick-look figure (default: ipf / parent_ipf / ...)
    keep_scratch: bool = False                # keep cache/compare_<step>/ (per-backend outputs) for inspection
    resolved: dict[str, Any] | None = None    # filled in: the step references both runs used


@op("compare.backends", CompareBackendsParams, uses_backend=False)
def _compare_backends(ctx: OpContext) -> None:
    """Run one backend operation on both backends (in a scratch copy of the project) and compare: every number of
    its JSON outputs side by side (absolute / relative difference) plus both figures, each drawn by its own engine."""
    p = CompareBackendsParams.model_validate(ctx.params)
    if p.op not in OPS or not OPS[p.op].uses_backend:
        raise ValueError(f"compare.backends needs a backend operation, one of {comparable_ops()}; got {p.op!r}")
    if p.backends[0] == p.backends[1]:
        raise ValueError("compare.backends needs two different backends")
    for b in p.backends:
        reason = get_backend(b).unavailable_reason()
        if reason:
            raise BackendUnavailable(f"{b} backend unavailable: {reason}")
    params = OPS[p.op].params.model_validate(p.params).model_dump(mode="json")
    scratch = scratch_copy(ctx.project, f"compare_{ctx.step.id}")
    runs: dict[str, RecipeStep] = {}
    figs: dict[str, RecipeStep] = {}
    try:
        for b in p.backends:
            if runs:   # second engine: use the same earlier steps the first one resolved
                first = next(iter(runs.values()))
                params = {**params, **{k: first.params[k] for k in REF_FIELDS
                                       if params.get(k) is None and first.params.get(k) is not None}}
            runs[b] = run(scratch, p.op, params, backend=b)
            if p.figure and not any(r.kind == "png" for r in runs[b].outputs.values()):
                spec = default_figure(p.op, runs[b], p.figure_kind)
                if spec:
                    figs[b] = run(scratch, spec[0], {k: v for k, v in spec[1].items() if v is not None}, backend=b)
        _collect(ctx, scratch, p, runs, figs)
    finally:
        release_project(scratch)
        if not p.keep_scratch:
            shutil.rmtree(scratch.root, ignore_errors=True)
    ctx.params.update(params=p.params, resolved={k: params.get(k) for k in REF_FIELDS if params.get(k)})
    ctx.step.versions = {f"{b}.{k}": v for b in p.backends for k, v in runs[b].versions.items()}


def _collect(ctx: OpContext, scratch: Project, p: CompareBackendsParams, runs: dict[str, RecipeStep],
             figs: dict[str, RecipeStep]) -> None:
    a, b = p.backends
    nums: dict[str, dict[str, Any]] = {}
    for be, st in runs.items():
        d: dict[str, Any] = {}
        for name, ref in st.outputs.items():
            if ref.kind in NUMBER_OUTPUT_KINDS:
                d.update(flatten_numbers(json.loads((scratch.root / ref.path).read_text(encoding="utf-8")), name))
        own = {st.id: "<this run>", **({figs[be].id: "<this figure>"} if be in figs else {})}
        nums[be] = {k: own.get(v, v) if isinstance(v, str) else v for k, v in d.items()
                    if k.rsplit(".", 1)[-1] != "backend"}
    rows = compare_rows(nums[a], nums[b])
    numeric = [r for r in rows if r["rel_diff"] is not None]
    rel = np.array([r["rel_diff"] for r in numeric]) if numeric else np.zeros(0)
    table = {
        "op": p.op, "backends": [a, b], "map_id": runs[a].params.get("map_id"),
        "versions": {be: runs[be].versions for be in p.backends},
        "params": {be: runs[be].params for be in p.backends},
        "rows": rows,
        "n_values": len(rows), "n_numeric": len(numeric), "n_identical": int(sum(r["same"] for r in rows)),
        "max_rel_diff": float(rel.max()) if rel.size else 0.0,
        "n_rel_diff_over_1pct": int((rel > 0.01).sum()),
        "differing_strings": [r["key"] for r in rows if r["rel_diff"] is None and not r["same"]],
    }
    ctx.output("table", "json", table)
    ctx.metrics.update(n_values=len(rows), max_rel_diff=round(table["max_rel_diff"], 6),
                       n_rel_diff_over_1pct=table["n_rel_diff_over_1pct"],
                       **{f"{be}_{k}": runs[be].metrics[k] for be in p.backends for k in PERF_KEYS
                          if k in runs[be].metrics})   # run time / RAM: metrics, not compared on replay
    # figures: each engine's own PNG (first PNG output of the op, else the quick-look figure)
    pngs: dict[str, Path] = {}
    for be in p.backends:
        st = figs.get(be) or runs[be]
        ref = next((r for r in st.outputs.values() if r.kind == "png"), None)
        if ref is not None:
            pngs[be] = scratch.root / ref.path
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    for be, f in pngs.items():
        dst = ctx.out_dir / f"figure_{be}.png"
        shutil.copy2(f, dst)
        ctx.output(f"figure_{be}", "png", dst)
    if len(pngs) == 2:
        out = montage([(_engine_label(be, runs[be].versions), pngs[be]) for be in p.backends],
                      ctx.out_dir / "side_by_side.png")
        ctx.output("side_by_side", "png", out)
