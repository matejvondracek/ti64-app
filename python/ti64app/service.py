"""Service layer: every GUI/MCP/CLI action runs here as a recorded recipe step.

    step = run(project, op, params)            # executes, writes outputs, appends to recipe.json
    report = replay(project)                   # re-runs all steps in a scratch project, compares outputs

Operations: sample.set, ebsd.load, up2.header, bse.load (here) and the EBSD operations in
ops_ebsd.py (cleanup, grains, maps, figures) and ops_recon.py
(prior-beta reconstruction, its figures, parameter sweep). Backend-dependent ops use the project's backend
(Settings) unless `backend=` overrides it.

Objects (maps, grains) stay inside the backend; SESSION remembers which backend object belongs to
which recipe step. If it is gone (new process, other backend), materialize() rebuilds it by re-running
the steps it depends on, without recording them again.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil
from pydantic import BaseModel

from . import __version__
from .backends import get_backend
from .backends.base import BackendUnavailable, EBSDData, EBSDHandle, LoadParams
from .hashing import array_hash, file_fingerprint, json_hash, npz_hash, png_hash
from .io import bse as bse_io
from .io import up2 as up2_io
from .project import Project, write_json
from .recipe import FileInput, OutputRef, RecipeStep
from .sample import SampleSheet


# ---- op registry ------------------------------------------------------------------------------
@dataclass
class OpContext:
    project: Project
    step: RecipeStep
    params: dict[str, Any]
    backend: Any = None
    out_dir: Path = Path()
    outputs: dict[str, tuple[str, Any]] = field(default_factory=dict)   # name -> (kind, value)
    metrics: dict[str, float] = field(default_factory=dict)

    def add_input(self, path: str | Path) -> None:
        p = Path(path)
        self.step.inputs.append(FileInput(path=str(p), fingerprint=file_fingerprint(p)))

    def output(self, name: str, kind: str, value: Any) -> None:
        self.outputs[name] = (kind, value)

    def remember(self, handle: Any) -> None:
        """Keep the backend object produced by this step for later steps."""
        SESSION[(str(self.project.root), self.step.id, self.backend.name)] = handle

    def use(self, step_id: str, output: str = "ebsd") -> Any:
        """Backend object of an earlier step (rebuilt if needed); recorded as an input."""
        ref = f"{step_id}:{output}"
        if ref not in self.step.inputs:
            self.step.inputs.append(ref)
        return materialize(self.project, step_id, self.backend.name)


@dataclass
class OpSpec:
    func: Callable[[OpContext], None]
    params: type[BaseModel]
    uses_backend: bool
    doc: str


OPS: dict[str, OpSpec] = {}


def op(name: str, params: type[BaseModel], uses_backend: bool):
    def deco(func):
        OPS[name] = OpSpec(func, params, uses_backend, (func.__doc__ or "").strip())
        return func
    return deco


# backend objects still alive: (project root, step id, backend) -> handle
SESSION: dict[tuple[str, str, str], Any] = {}


# ---- operations -------------------------------------------------------------------------------
class SampleSetParams(SampleSheet):
    pass


@op("sample.set", SampleSetParams, uses_backend=False)
def _sample_set(ctx: OpContext) -> None:
    """Store the sample sheet of one map (build direction, input state, 4PB, position)."""
    sheet = SampleSheet.model_validate(ctx.params)
    f = Path(sheet.map_file)
    if not f.is_file():
        raise FileNotFoundError(f"map file not found: {f}")
    ctx.add_input(f)
    write_json(ctx.project.samples_dir / f"{sheet.map_id}.json", sheet.model_dump(mode="json"))
    ctx.output("sample", "json", sheet.model_dump(mode="json"))


class EbsdLoadParams(BaseModel):
    map_id: str
    setting: int = 2


@op("ebsd.load", EbsdLoadParams, uses_backend=True)
def _ebsd_load(ctx: OpContext) -> None:
    """Load the .ang of a map (input state from its sample sheet) with the active backend."""
    p = EbsdLoadParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    ctx.add_input(sheet.map_file)
    lp = LoadParams(file=Path(sheet.map_file), input_state=sheet.input_state, setting=p.setting)
    t0 = time.perf_counter()
    h = ctx.backend.load_ebsd(lp)
    t1 = time.perf_counter()
    data = ctx.backend.get_ebsd(h)
    t2 = time.perf_counter()
    ctx.remember(h)
    ctx.metrics.update(load_s=round(t1 - t0, 2), transfer_s=round(t2 - t1, 2), n_points=data.n)
    ctx.output("ebsd", "npz", data)
    ctx.output("meta", "json", {**data.meta(), "input_state": sheet.input_state, "map_id": p.map_id})


class Up2HeaderParams(BaseModel):
    file: str


@op("up2.header", Up2HeaderParams, uses_backend=False)
def _up2_header(ctx: OpContext) -> None:
    """Read the header of an EDAX .up2 pattern file (no patterns are loaded)."""
    f = Path(Up2HeaderParams.model_validate(ctx.params).file)
    ctx.add_input(f)
    h = up2_io.read_header(f)
    d = {k: getattr(h, k) for k in h.__dataclass_fields__}
    d.update(n_patterns=h.n_patterns, expected_size=h.expected_size, size_ok=h.size_ok)
    ctx.output("header", "json", d)


class BseLoadParams(BaseModel):
    image: str
    txt: str | None = None


@op("bse.load", BseLoadParams, uses_backend=False)
def _bse_load(ctx: OpContext) -> None:
    """Load a JEOL BSE image and its .txt metadata."""
    p = BseLoadParams.model_validate(ctx.params)
    txt = p.txt or (str(bse_io.find_sidecar(p.image)) if bse_io.find_sidecar(p.image) else None)
    img, meta = bse_io.load_bse(p.image, txt)
    ctx.add_input(p.image)
    ctx.add_input(txt)
    ctx.output("image", "npz", {"image": img})
    ctx.output("meta", "json", meta.model_dump(mode="json"))


# ---- running ------------------------------------------------------------------------------------
def _write_output(ctx: OpContext, name: str, kind: str, value: Any) -> OutputRef:
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "npz":
        arrays = value.to_arrays() if isinstance(value, EBSDData) else value
        path = ctx.out_dir / f"{name}.npz"
        np.savez(path, **arrays)
        h = array_hash(arrays)
    elif kind == "json":
        path = ctx.out_dir / f"{name}.json"
        write_json(path, value)
        h = json_hash(json.loads(path.read_text(encoding="utf-8")))
    elif kind == "png":                       # value = path of a PNG written by the backend renderer
        path = ctx.out_dir / f"{name}.png"
        if Path(value) != path:
            shutil.move(str(value), path)
        h = png_hash(path)
    else:
        raise ValueError(kind)
    return OutputRef(path=str(path.relative_to(ctx.project.root).as_posix()), kind=kind, content_hash=h)


def _memory_GB() -> dict[str, float]:
    mi = psutil.Process().memory_info()
    out = {"rss_GB": round(mi.rss / 2**30, 3)}
    if hasattr(mi, "peak_wset"):
        out["peak_GB"] = round(mi.peak_wset / 2**30, 3)     # Windows: process lifetime peak
    return out


def _backend(project: Project, spec: OpSpec, backend: str | None):
    if not spec.uses_backend:
        return None
    name = backend or project.info().backend
    be = get_backend(name)
    reason = be.unavailable_reason()
    if reason:
        raise BackendUnavailable(f"{name} backend unavailable: {reason}")
    return be


def run(project: Project, op_name: str, params: dict[str, Any] | BaseModel,
        backend: str | None = None, record: bool = True, step_id: str | None = None) -> RecipeStep:
    """Execute one operation, write its outputs and (by default) append it to the recipe.
    `step_id` is only given by replay, so references between steps stay valid."""
    if op_name not in OPS:
        raise KeyError(f"unknown operation {op_name!r}; known: {sorted(OPS)}")
    spec = OPS[op_name]
    params = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
    params = spec.params.model_validate(params).model_dump(mode="json")
    be = _backend(project, spec, backend)
    step = RecipeStep(op=op_name, params=params, backend=be.name if be else None,
                      versions=be.versions() if be else {"app": __version__})
    if step_id:
        step.id = step_id
    ctx = OpContext(project, step, params, be, project.outputs_dir / step.id)
    t0 = time.perf_counter()
    try:
        spec.func(ctx)
        step.params = ctx.params                 # ops record resolved values (e.g. source step, preset)
        step.outputs = {n: _write_output(ctx, n, k, v) for n, (k, v) in ctx.outputs.items()}
    except Exception:
        shutil.rmtree(ctx.out_dir, ignore_errors=True)
        raise
    step.metrics = {**ctx.metrics, "seconds": round(time.perf_counter() - t0, 2), **_memory_GB()}
    if record:
        r = project.recipe()
        if op_name == "sample.set":      # a changed sample sheet makes everything on that map stale
            mid = params["map_id"]
            r.mark_stale(lambda s: s.params.get("map_id") == mid)
        r.add(step)
        project.save_recipe(r)
    return step


# ---- replay -------------------------------------------------------------------------------------
class OutputCheck(BaseModel):
    step_id: str
    op: str
    output: str
    status: str                 # identical | within_tolerance | different | missing
    detail: str = ""


class ReplayReport(BaseModel):
    replay_root: str
    backend_override: str | None
    checks: list[OutputCheck]
    input_changes: list[str]

    @property
    def ok(self) -> bool:
        return not self.input_changes and all(c.status in ("identical", "within_tolerance") for c in self.checks)


def _compare_npz(a: Path, b: Path) -> tuple[bool, str]:
    with np.load(a) as za, np.load(b) as zb:
        if set(za.files) != set(zb.files):
            return False, f"arrays differ: {sorted(set(za.files) ^ set(zb.files))}"
        worst = []
        for k in za.files:
            x, y = za[k], zb[k]
            if x.shape != y.shape:
                return False, f"{k}: shape {x.shape} vs {y.shape}"
            if k == "quat":
                ok = np.isnan(x[:, 0]) == np.isnan(y[:, 0])
                m = ~np.isnan(x[:, 0])
                dot = np.abs(np.sum(x[m] * y[m], axis=1)).clip(0, 1)
                ang = float(np.degrees(2 * np.arccos(dot)).max()) if m.any() else 0.0
                if not ok.all() or ang > 1e-3:
                    return False, f"quat: max rotation difference {ang:.3g} deg"
                worst.append(f"quat {ang:.2g} deg")
            elif np.issubdtype(x.dtype, np.floating):
                if not np.allclose(x, y, rtol=1e-6, atol=1e-5, equal_nan=True):
                    return False, f"{k}: max |diff| {np.nanmax(np.abs(x - y)):.3g}"
            elif not np.array_equal(x, y):
                return False, f"{k}: {int(np.sum(x != y))} values differ"
        return True, "; ".join(worst)


def replay(project: Project, backend: str | None = None, into: Path | None = None,
           include_stale: bool = False) -> ReplayReport:
    """Re-run the recipe in a scratch project and compare every output with the recorded one.
    Backend steps use the recorded backend unless `backend` overrides it."""
    recipe = project.recipe()
    root = into or project.cache_dir / f"replay_{datetime.now():%Y%m%d_%H%M%S}"
    if root.exists():
        shutil.rmtree(root)
    scratch = Project.create(root.name, base=root.parent, backend=project.info().backend)
    checks: list[OutputCheck] = []
    changes: list[str] = []
    for s in recipe.steps:
        if s.stale and not include_stale:
            continue
        for inp in s.inputs:
            if isinstance(inp, FileInput):
                p = Path(inp.path)
                if not p.is_file():
                    changes.append(f"{s.id}: input missing: {p}")
                elif file_fingerprint(p) != inp.fingerprint:
                    changes.append(f"{s.id}: input changed: {p}")
        be = (backend or s.backend) if s.backend else None
        new = run(scratch, s.op, s.params, backend=be, step_id=s.id)
        for name, ref in s.outputs.items():
            nref = new.outputs.get(name)
            if nref is None:
                checks.append(OutputCheck(step_id=s.id, op=s.op, output=name, status="missing"))
            elif nref.content_hash == ref.content_hash:
                checks.append(OutputCheck(step_id=s.id, op=s.op, output=name, status="identical"))
            elif ref.kind == "npz":
                ok, detail = _compare_npz(project.root / ref.path, scratch.root / nref.path)
                checks.append(OutputCheck(step_id=s.id, op=s.op, output=name,
                                          status="within_tolerance" if ok else "different", detail=detail))
            else:
                a = json.loads((project.root / ref.path).read_text(encoding="utf-8"))
                b = json.loads((scratch.root / nref.path).read_text(encoding="utf-8"))
                diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)) if isinstance(a, dict) else []
                checks.append(OutputCheck(step_id=s.id, op=s.op, output=name, status="different",
                                          detail=f"keys: {diff}"))
    return ReplayReport(replay_root=str(scratch.root), backend_override=backend, checks=checks,
                        input_changes=changes)


def materialize(project: Project, step_id: str, backend: str) -> Any:
    """Backend object produced by an earlier step; rebuilt by re-running that step (and, through
    ctx.use, its own sources) without recording it or writing outputs."""
    key = (str(project.root), step_id, backend)
    be = get_backend(backend)
    h = SESSION.get(key)
    if h is not None and h.id in be.handles():
        return h
    s = project.recipe().step(step_id)
    ctx = OpContext(project, RecipeStep(op=s.op, params=dict(s.params), backend=backend), dict(s.params),
                    be, project.cache_dir / "rebuild")
    ctx.step.id = step_id
    OPS[s.op].func(ctx)
    if key not in SESSION:
        raise RuntimeError(f"step {step_id} ({s.op}) does not produce a backend object")
    return SESSION[key]


def latest_step(project: Project, ops: tuple[str, ...], **match) -> RecipeStep | None:
    """Most recent non-stale step whose op is in `ops` (earlier entries preferred) and whose params
    contain the given values."""
    steps = [s for s in project.recipe().steps if not s.stale
             and all(s.params.get(k) == v for k, v in match.items())]
    for o in ops:
        hits = [s for s in steps if s.op == o]
        if hits:
            return hits[-1]
    return None


def verify_outputs(project: Project) -> list[str]:
    """Check that the stored output files still have their recorded content hashes."""
    bad = []
    for s in project.recipe().steps:
        for name, ref in s.outputs.items():
            p = project.root / ref.path
            if not p.is_file():
                bad.append(f"{s.id}:{name} missing")
                continue
            h = (npz_hash(p) if ref.kind == "npz" else png_hash(p) if ref.kind == "png"
                 else json_hash(json.loads(p.read_text(encoding="utf-8"))))
            if h != ref.content_hash:
                bad.append(f"{s.id}:{name} changed")
    return bad


def ops_catalog() -> dict[str, dict]:
    return {n: {"doc": s.doc, "uses_backend": s.uses_backend, "params": s.params.model_json_schema()}
            for n, s in OPS.items()}


from . import ops_ebsd  # noqa: E402,F401  (registers the EBSD operations)
from . import ops_recon  # noqa: E402,F401  (registers the reconstruction operations)
