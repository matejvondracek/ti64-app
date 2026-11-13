"""Service layer: every GUI/MCP/CLI action runs here as a recorded recipe step.

    step = run(project, op, params)            # executes, writes outputs, appends to recipe.json
    report = replay(project)                   # re-runs all steps in a scratch project, compares outputs

Operations: sample.set, ebsd.load, up2.header, bse.load (here), sem.stitch (ops_stitch.py, SEM image mosaics) and the EBSD operations in
ops_ebsd.py (cleanup, grains, maps, figures), ops_recon.py
(prior-beta reconstruction, its figures, parameter sweep) and ops_grainsize.py (grain size, phase 4). Backend-dependent ops use the project's backend
(Settings) unless `backend=` overrides it.

Objects (maps, grains) stay inside the backend; SESSION remembers which backend object belongs to
which recipe step. If it is gone (new process, other backend), materialize() rebuilds it by re-running
the steps it depends on, without recording them again.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil
from pydantic import BaseModel

from . import __version__, exportlog, history, progress
from .backends import get_backend
from .backends.base import BackendUnavailable, EBSDData, EBSDHandle, EngineRestarted, LoadParams
from .hashing import array_hash, bytes_hash, file_fingerprint, json_hash, npz_hash, png_hash, xlsx_hash
from .io import bse as bse_io
from .io import up2 as up2_io
from .project import Project, write_json
from .exportlog import EXPORT_OPS, ExportEntry
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
    outputs: dict[str, tuple] = field(default_factory=dict)   # name -> (kind, value[, file stem])
    metrics: dict[str, float] = field(default_factory=dict)
    need_object: bool = False                 # materialize(): the backend object is wanted (no cache shortcut)
    figure_ext: str = ".png"                  # figure ops: file type of the figure ("Save" / "Export" of a result)

    def add_input(self, path: str | Path) -> None:
        p = Path(path)
        self.step.inputs.append(FileInput(path=str(p), fingerprint=file_fingerprint(p)))

    def output(self, name: str, kind: str, value: Any, stem: str | None = None) -> None:
        """Register an output; `stem` names the file (default: the output name)."""
        self.outputs[name] = (kind, value, stem or name)

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
# plain-language notices for the GUI (e.g. "MATLAB stopped unexpectedly and was restarted"), taken by pop_notices()
NOTICES: list[str] = []
log = logging.getLogger("ti64app.service")


def pop_notices() -> list[str]:
    out = list(NOTICES)
    NOTICES.clear()
    return out


# ---- operations -------------------------------------------------------------------------------
class SampleSetParams(SampleSheet):
    pass


@op("sample.set", SampleSetParams, uses_backend=False)
def _sample_set(ctx: OpContext) -> None:
    """Store the sample sheet of one map (build direction, input state, 4PB, position)."""
    sheet = SampleSheet.model_validate(ctx.params)
    f = Path(sheet.map_file)
    if not f.is_absolute():                  # recipes may use paths relative to the working directory
        f = f.absolute()
        sheet.map_file = str(f)
        ctx.params["map_file"] = str(f)
    if not f.is_file():
        raise FileNotFoundError(f"map file not found: {f}")
    ctx.add_input(f)
    ctx.project.set_sheet(sheet)             # sample (specimen) + linked file with its map sheet (phase 6.6)
    ctx.output("sample", "json", sheet.model_dump(mode="json"))


LOAD_CACHE_VERSION = 3       # 2: phases renamed by symmetry, MATLAB single-phase numbering fixed (fix 4); 3: + frame
                             # sidecar (crystal reference frame per phase, fix 5)


class EbsdLoadParams(BaseModel):
    map_id: str
    setting: int = 2


@op("ebsd.load", EbsdLoadParams, uses_backend=True)
def _ebsd_load(ctx: OpContext) -> None:
    """Load the .ang of a map (input state from its sample sheet) with the active backend."""
    p = EbsdLoadParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    ctx.add_input(sheet.map_file)
    # phases by crystal symmetry, renamed to 'Ti (alpha)' / 'Ti (beta)' in both backends (ti64app.phases); stops with
    # a map-sheet message when the symmetry does not decide and the user has not chosen
    phases = []
    if Path(sheet.map_file).suffix.lower() == ".ang":
        from .phases import for_file  # noqa: PLC0415

        phases = for_file(sheet.map_file, sheet.phase_map, p.map_id)
    lp = LoadParams(file=Path(sheet.map_file), input_state=sheet.input_state, setting=p.setting,
                    phase_names={ph.id: ph.name for ph in phases})
    extra = {"input_state": sheet.input_state, "map_id": p.map_id, "phases": [ph.as_dict() for ph in phases]}
    # loaded-map cache per backend (CLAUDE.md): keyed by file fingerprint, input state, setting, phase names, backend +
    # versions. A recorded load whose map is cached takes its data from there; the backend object is made only when a
    # later step needs it (materialize -> need_object)
    key = json_hash([ctx.step.inputs[-1].fingerprint, lp.input_state, lp.setting, ctx.backend.name,
                     ctx.backend.versions(), {str(k): v for k, v in lp.phase_names.items()}, LOAD_CACHE_VERSION])[7:31]
    cdir = ctx.project.cache_dir / "maps"
    cnpz, cmeta = cdir / f"{ctx.backend.name}_{key}.npz", cdir / f"{ctx.backend.name}_{key}.json"
    t0 = time.perf_counter()
    progress.report("reading file", 0.05)
    if not ctx.need_object and cnpz.is_file() and cmeta.is_file():
        try:
            meta = json.loads(cmeta.read_text(encoding="utf-8"))
            data = EBSDData.from_npz(cnpz, meta)
            ctx.metrics.update(cached=1.0, load_s=round(time.perf_counter() - t0, 2), n_points=data.n)
            ctx.output("ebsd", "npz", data)
            ctx.output("meta", "json", {**data.meta(), **extra})
            fside = cdir / f"{ctx.backend.name}_{key}.frame.json"
            data.alignment = ({int(k): v for k, v in json.loads(fside.read_text(encoding="utf-8")).items()}
                              if fside.is_file() else {})
            ctx.output("frame", "json", load_frame(sheet.map_file, data, ctx.backend.name))
            return
        except (OSError, ValueError, KeyError):
            pass                                          # damaged cache entry: load again
    h = ctx.backend.load_ebsd(lp)
    t1 = time.perf_counter()
    progress.report("transferring data", 0.35)
    data = ctx.backend.get_ebsd(h)
    t2 = time.perf_counter()
    ctx.remember(h)
    ctx.metrics.update(load_s=round(t1 - t0, 2), transfer_s=round(t2 - t1, 2), n_points=data.n)
    ctx.output("ebsd", "npz", data)
    ctx.output("meta", "json", {**data.meta(), **extra})
    ctx.output("frame", "json", load_frame(sheet.map_file, data, ctx.backend.name))
    if not cnpz.is_file():
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            tmp = cdir / f".{cnpz.stem}.{time.time_ns()}.npz"
            data.save_npz(tmp)
            tmp.replace(cnpz)
            write_json(cmeta, data.meta())
            write_json(cdir / f"{ctx.backend.name}_{key}.frame.json", {str(k): v for k, v in data.alignment.items()})
        except OSError:
            pass


def load_frame(map_file: str, data: EBSDData, backend: str) -> dict:
    """The 'frame' output of a load (fix 5, docs/orientation_check.md): the .ang's CartesianAlignment line, the
    crystal frame per phase as the backend loaded it, a warning when they disagree, the EDAX setting and which OIM
    sample axis each app axis is."""
    from . import frame  # noqa: PLC0415

    header = None
    if str(map_file).lower().endswith(".ang"):
        from .io.ang import read_header  # noqa: PLC0415

        header = read_header(map_file).fields.get("CartesianAlignment")
    warn = frame.check_alignment(header)
    al = {str(k): v for k, v in data.alignment.items()}
    if not warn and any("x||a*" in v.replace(" ", "").lower() for v in al.values()):
        warn = f"{backend} loaded a hexagonal phase with X||a* although the .ang says '{header}' (X||a expected)"
    if warn:
        log.warning("%s: %s", map_file, warn)
    return {"cartesian_alignment": header, "crystal_frame": al or "not checked (map read from the load cache)",
            "backend": backend, "setting": data.setting, "oim_axes": frame.axis_text(data.setting), "warning": warn}


class Up2HeaderParams(BaseModel):
    file: str


@op("up2.header", Up2HeaderParams, uses_backend=False)
def _up2_header(ctx: OpContext) -> None:
    """Read the header of an EDAX .up2 pattern file (no patterns are loaded)."""
    f = Path(Up2HeaderParams.model_validate(ctx.params).file)
    ctx.add_input(f)
    h = up2_io.read_header(f)
    d = {k: getattr(h, k) for k in h.__dataclass_fields__}
    d.update(n_patterns=h.n_patterns, n_points=h.n_points, n_padding=h.n_padding, expected_size=h.expected_size,
             size_ok=h.size_ok)
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
EXTENSIONS = {"tiff": ".tif", "svg": ".svg", "pdf": ".pdf", "mosaic": ".tif"}


def _write_output(ctx: OpContext, name: str, kind: str, value: Any, stem: str | None = None) -> OutputRef:
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    name = stem or name
    if kind == "npz":
        arrays = value.to_arrays() if isinstance(value, EBSDData) else value
        path = ctx.out_dir / f"{name}.npz"
        np.savez(path, **arrays)
        h = array_hash(arrays)
    elif kind == "json":
        path = ctx.out_dir / f"{name}.json"
        write_json(path, value)
        h = json_hash(json.loads(path.read_text(encoding="utf-8")))
    elif kind == "csv":                       # value = table {column: 1-D array}
        path = ctx.out_dir / f"{name}.csv"
        write_csv(path, value)
        h = bytes_hash(path)
    elif kind == "xlsx":                      # value = {sheet name: table {column: 1-D array}}
        path = ctx.out_dir / f"{name}.xlsx"
        write_xlsx(path, value)
        h = xlsx_hash(path)
    elif kind in ("png", "tiff"):             # value = path of an image written by a renderer
        path = ctx.out_dir / f"{name}{EXTENSIONS.get(kind, '.png')}"
        if Path(value) != path:
            shutil.move(str(value), path)
        h = png_hash(path)
    elif kind == "mosaic":                    # large TIFF (tifffile, deterministic bytes): hashed without decoding
        path = ctx.out_dir / f"{name}{EXTENSIONS[kind]}"
        if Path(value) != path:
            shutil.move(str(value), path)
        h = bytes_hash(path)
    elif kind in ("svg", "pdf"):              # vector exports: bytes (renderers write fixed metadata if they can)
        path = ctx.out_dir / f"{name}{EXTENSIONS[kind]}"
        if Path(value) != path:
            shutil.move(str(value), path)
        h = bytes_hash(path)
    else:
        raise ValueError(kind)
    return OutputRef(path=str(path.relative_to(ctx.project.root).as_posix()), kind=kind, content_hash=h)


def _frame(table: dict):
    import pandas as pd  # noqa: PLC0415

    return pd.DataFrame({k: np.asarray(v) for k, v in table.items()})


def write_csv(path: Path, table: dict) -> None:
    _frame(table).to_csv(path, index=False, lineterminator="\n")


def write_xlsx(path: Path, sheets: dict[str, dict]) -> None:
    import pandas as pd  # noqa: PLC0415

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, table in sheets.items():
            _frame(table).to_excel(xw, sheet_name=name[:31], index=False)


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
        backend: str | None = None, record: bool = True, step_id: str | None = None,
        out_dir: Path | None = None, replace: str | None = None) -> RecipeStep:
    """Execute one operation, write its outputs and (by default) append it to the recipe.
    `step_id` is only given by replay, so references between steps stay valid. Exports are not recipe steps: an
    export op called with record=True is run through export() (export log) instead. `replace` = id of a step the new
    one takes the place of in the recipe (tuning, see apply()); undo brings the old one back."""
    if op_name in EXPORT_OPS and record:
        return export(project, op_name, params, backend)[0]
    if op_name not in OPS:
        raise KeyError(f"unknown operation {op_name!r}; known: {sorted(OPS)}")
    spec = OPS[op_name]
    params = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
    params = spec.params.model_validate(params).model_dump(mode="json")
    if record and step_id is None and op_name != "sample.set" and params.get("map_id"):
        record_sheet(project, params["map_id"])
    be = _backend(project, spec, backend)
    step = RecipeStep(op=op_name, params=params, backend=be.name if be else None,
                      versions=be.versions() if be else {"app": __version__})
    if step_id:
        step.id = step_id
    step.sample_id = step_sample(project, op_name, params)
    ctx = OpContext(project, step, params, be, out_dir or project.step_dir(step.id, step.sample_id))
    before = history.snapshot(project) if record else None     # undo point (state files only)
    if record:
        _mark_running(project, step)
    from . import breadcrumbs  # noqa: PLC0415

    what = f"{op_name} {params.get('map_id') or ''}".strip() + ("" if record else " (not recorded)")
    breadcrumbs.add("step", f"start {what} [{be.name if be else 'no backend'}]")
    t0 = time.perf_counter()
    try:
        try:
            spec.func(ctx)
        except EngineRestarted as e:         # MATLAB died and was restarted: run the step once more (its sources
            log.warning("%s; rerunning %s", e, op_name)   # are rebuilt from the recipe by materialize())
            step.inputs = []
            ctx = OpContext(project, step, dict(params), be, ctx.out_dir)
            shutil.rmtree(ctx.out_dir, ignore_errors=True)
            spec.func(ctx)
            step.metrics["engine_restarted"] = 1.0
            NOTICES.append(f"{e} The step '{op_name}' was run again after the restart.")
        step.params = ctx.params                 # ops record resolved values (e.g. source step, preset)
        step.outputs = {n: _write_output(ctx, n, *o) for n, o in ctx.outputs.items()}
    except Exception as e:
        breadcrumbs.add("step", f"FAILED {what} after {time.perf_counter() - t0:.1f} s: {type(e).__name__}: {e}")
        shutil.rmtree(ctx.out_dir, ignore_errors=True)
        if before:
            history.discard(project, before)
        if record:
            project.clear_interrupted()
        raise
    step.metrics = {**step.metrics, **ctx.metrics, "seconds": round(time.perf_counter() - t0, 2), **_memory_GB()}
    breadcrumbs.add("step", f"done {what} ({step.id}) in {step.metrics['seconds']:g} s")
    if record:
        project.clear_interrupted()
        for i in step.inputs:                    # size + mtime of linked files when used ("changed" warning)
            if not isinstance(i, str):
                project.touch_file(i.path)
        r = project.recipe()
        if op_name == "sample.set":      # a changed sheet makes the results that depend on it stale (not: notes)
            from .sample import stale_on_sheet_change  # noqa: PLC0415

            mid = params["map_id"]
            prev = [s for s in r.steps if s.op == "sample.set" and s.params.get("map_id") == mid and not s.stale]
            pred = stale_on_sheet_change(mid, prev[-1].params if prev else None, params)
            r.mark_stale(lambda s: (s.op == "sample.set" and s.params.get("map_id") == mid) or pred(s))
        old = next((k for k, s in enumerate(r.steps) if s.id == replace), None) if replace else None
        if old is None:
            r.add(step)
        else:
            r.steps[old] = step
        project.save_recipe(r)
        history.commit(project, f"{op_name} {step.params.get('map_id', '')} ({step.id})".replace("  ", " ")
                       + (f", replaces {replace}" if old is not None else ""), before)
        if old is not None:
            _release_steps(project, [replace])
        if step_id is None:
            release_stale(project)
    return step


# ---- tuning replaces, not appends (phase 6.6 fix 5) -------------------------------------------------------------
NOT_TUNED = ("sample.set", "annotations.set", "ebsd.crop", "compare.backends", "compare.oim")


def tune_key(project: Project, op_name: str, params: dict) -> tuple | None:
    """What makes two steps 'the same operation' for tuning: the op plus the cleanup operation (ebsd.clean), the map
    kind (map.compute: KAM vs GND; figures), the grain-size target and whether grains are alpha or prior-beta grains.
    None = never replaced (sheet records, annotations, crops, comparisons)."""
    if op_name in NOT_TUNED or op_name in EXPORT_OPS:
        return None
    if op_name == "ebsd.clean":
        return (op_name, params.get("operation"))
    if op_name in ("map.compute", "figure", "recon.figure", "grainsize.figure"):
        return (op_name, params.get("kind"))
    if op_name == "grainsize.run":
        return (op_name, params.get("target") or "alpha")
    if op_name == "grains.calc":
        src = params.get("source")
        try:
            beta = bool(src) and project.recipe().step(src).op == "recon.run"
        except KeyError:
            beta = False
        return (op_name, beta)
    return (op_name,)


def tuning_target(project: Project, op_name: str, params: dict) -> RecipeStep | None:
    """The step a new step would replace: the NEWEST current step of the same map (sheet records aside) when it is the
    same operation (tune_key) and no other step uses it; None = append."""
    from .recipe import references  # noqa: PLC0415

    mid = params.get("map_id")
    key = tune_key(project, op_name, params)
    if not mid or key is None:
        return None
    steps = project.recipe().steps
    mine = [s for s in steps if not s.stale and s.params.get("map_id") == mid and s.op != "sample.set"]
    if not mine or tune_key(project, mine[-1].op, mine[-1].params) != key:
        return None
    last = mine[-1]
    known = {s.id for s in steps}
    if any(last.id in references(s, known) for s in steps if s.id != last.id):
        return None
    return last


def apply(project: Project, op_name: str, params: dict[str, Any] | BaseModel, backend: str | None = None,
          tune: bool = True) -> RecipeStep:
    """Run a step from the GUI / MCP: like run(), but when the newest step of the map is the same operation (the
    user is tuning its parameters), the new step REPLACES it instead of being appended (undo restores the previous
    parameters). A replaced cleanup / load keeps the source map of the step it replaces. Recorded recipes are never
    rewritten this way: replay and run_recipe use run()."""
    params = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
    old = tuning_target(project, op_name, params) if tune else None
    if old is not None and op_name in ("ebsd.clean",) and not params.get("source") and old.params.get("source"):
        params["source"] = old.params["source"]
    return run(project, op_name, params, backend, replace=old.id if old is not None else None)


from .sample import SHEET_RESULT_KEYS  # noqa: E402  (kept importable from here)


# ---- exports (not recipe steps, fix 3.4) -----------------------------------------------------------
def export(project: Project, op_name: str, params: dict[str, Any] | BaseModel, backend: str | None = None,
           target: str | Path | None = None) -> tuple[RecipeStep, ExportEntry]:
    """Run an export op WITHOUT recording it in the recipe: files go to exports/<id>/ in the project, a copy (+ the
    JSON sidecar) to `target` if given, and one entry (settings, file, date) is appended to export_log.json.
    Returns (the unrecorded step, for its outputs; the log entry)."""
    import uuid  # noqa: PLC0415

    if op_name not in EXPORT_OPS:
        raise ValueError(f"{op_name} is not an export")
    eid = uuid.uuid4().hex[:8]
    step = run(project, op_name, params, backend, record=False, step_id=eid, out_dir=project.root / "exports" / eid)
    file = ""
    if target:
        t = Path(target)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project.root / step.outputs["export"].path, t)
        if "sidecar" in step.outputs:
            shutil.copy2(project.root / step.outputs["sidecar"].path, t.with_suffix(".json"))
        file = str(t)
    e = ExportEntry(id=eid, op=op_name, params=step.params, backend=step.backend, versions=step.versions,
                    file=file, project_file=step.outputs["export"].path,
                    sidecar=step.outputs["sidecar"].path if "sidecar" in step.outputs else "",
                    sample_id=step.sample_id, map_id=step.params.get("map_id"),
                    seconds=float(step.metrics.get("seconds", 0.0)))
    exportlog.append(project.root, e)
    return step, e


def export_again(project: Project, entry_id: str, target: str | Path | None = None,
                 backend: str | None = None) -> tuple[RecipeStep | None, ExportEntry]:
    """Export again with the settings of a logged export (to `target`, default: the same file as before). Uses the
    recorded backend unless `backend` overrides it."""
    e = exportlog.get(project.root, entry_id)
    t = target if target is not None else (e.file or None)
    if e.op == RESULT_EXPORT:
        if t is None:
            raise ValueError("this export has no file to write again; choose one")
        return None, export_result(project, e.params["step"], e.params["format"], t)
    return export(project, e.op, e.params, backend or e.backend, t)


# ---- results: save the figure in the backend's editable format, export as an image (fix 3.14) ---------------
FIGURE_OPS = ("figure", "recon.figure", "grainsize.figure")
RESULT_EXPORT = "result.export"
EDITABLE_FORMATS = {"matlab": ("fig", "svg", "pdf"), "pymtex": ("svg", "pdf")}
EXPORT_FORMATS = ("png", "jpg", "tif", "svg", "pdf")


def render_figure_as(project: Project, step_id: str, fmt: str) -> Path:
    """Draw a recorded figure step again (same op, parameters and backend, not recorded) as `fmt`: png, tif, svg,
    pdf, fig (MATLAB only); jpg is converted from the PNG. Returns the file (in cache/save/)."""
    import uuid  # noqa: PLC0415

    s = project.recipe().step(step_id)
    if s.op not in FIGURE_OPS:
        raise ValueError(f"step {step_id} ({s.op}) is not a figure")
    be = get_backend(s.backend or project.info().backend)
    reason = be.unavailable_reason()
    if reason:
        raise BackendUnavailable(f"the figure was drawn by {be.name}, which is unavailable: {reason}")
    if fmt == "fig" and be.name != "matlab":
        raise ValueError("MATLAB .fig files can only be written by the MATLAB backend")
    ext = ".png" if fmt == "jpg" else "." + fmt
    out = project.cache_dir / "save" / uuid.uuid4().hex[:8]
    ctx = OpContext(project, RecipeStep(op=s.op, params=dict(s.params), backend=be.name), dict(s.params), be, out,
                    figure_ext=ext)
    ctx.step.id = f"save_{step_id}"
    out.mkdir(parents=True, exist_ok=True)
    OPS[s.op].func(ctx)
    f = Path(ctx.outputs["figure"][1])
    if fmt == "jpg":
        from PIL import Image  # noqa: PLC0415

        j = f.with_suffix(".jpg")
        with Image.open(f) as im:
            im.convert("RGB").save(j, "JPEG", quality=95, dpi=im.info.get("dpi", (300, 300)))
        f = j
    return f


def save_figure(project: Project, step_id: str, fmt: str, target: str | Path) -> Path:
    """'Save' of a figure result: the backend's editable format (MATLAB .fig; pymtex / MATLAB .svg or .pdf vector)."""
    t = Path(target)
    t.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(render_figure_as(project, step_id, fmt), t)
    return t


def export_result(project: Project, step_id: str, fmt: str, target: str | Path) -> ExportEntry:
    """'Export' of a figure result as png / jpg / tif / svg / pdf, logged in the export log (not a recipe step)."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"format {fmt!r}; choose one of {EXPORT_FORMATS}")
    t = save_figure(project, step_id, fmt, target)
    s = project.recipe().step(step_id)
    return exportlog.append(project.root, ExportEntry(op=RESULT_EXPORT, params={"step": step_id, "format": fmt,
                                                                                 "figure_op": s.op, **{k: v for k, v in s.params.items() if k in ("map_id", "kind")}},
                                                      backend=s.backend, versions=s.versions, file=str(t),
                                                      sample_id=s.sample_id, map_id=s.params.get("map_id")))


def record_sheet(project: Project, map_id: str) -> RecipeStep | None:
    """Sheets are edited on the Files tab (autosaved project state, phase 6.6). Before a recorded op uses a map, its
    current effective sheet is recorded as a sample.set step if it differs from the last recorded one, so the recipe
    alone replays the project (a new project gets the same sheets)."""
    try:
        sheet = project.sample(map_id).model_dump(mode="json")
    except (KeyError, ValueError):
        return None                               # the op itself reports what is missing
    last = latest_step(project, ("sample.set",), map_id=map_id)
    if last is not None and {k: last.params.get(k) for k in sheet} == sheet:
        return None
    return run(project, "sample.set", sheet)


def step_sample(project: Project, op_name: str, params: dict) -> str | None:
    """Sample (specimen) ID a step belongs to: explicit sample_id, the sample of its map, or the sheet's specimen."""
    if params.get("sample_id"):
        return params["sample_id"]
    if op_name == "sample.set":
        return params.get("specimen_id")
    if op_name == "compare.backends":
        return project.sample_of((params.get("params") or {}).get("map_id"))
    if op_name == "sem.export" and params.get("stitch"):          # the sample of the exported mosaic
        try:
            return project.recipe().step(params["stitch"]).sample_id
        except KeyError:
            return None
    return project.sample_of(params.get("map_id"))


def _mark_running(project: Project, step: RecipeStep) -> None:
    """running.json: if the app closes during the step, the next open reports it as interrupted (offer rerun)."""
    import os  # noqa: PLC0415

    write_json(project.root / "running.json",
               {"op": step.op, "params": step.params, "step_id": step.id, "sample_id": step.sample_id,
                "backend": step.backend, "pid": os.getpid(), "pid_started": psutil.Process().create_time(),
                "started": datetime.now().isoformat(timespec="seconds")})


def switch_backend(project: Project, backend: str) -> int:
    """Change the project's backend (undoable); marks results of the other backend stale. Returns how many."""
    if project.info().backend == backend:
        return 0
    before = history.snapshot(project)
    n = project.set_backend(backend)
    history.commit(project, f"backend -> {backend} ({n} steps stale)", before)
    return n


def plan_delete(project: Project, ids: list[str] | set[str], only_unused: bool = False) -> dict:
    """What deleting steps would remove. Steps that use a deleted step are deleted with it (their results would have
    no source), unless only_unused: then the steps that live steps still use are KEPT (used by 'Delete stale steps').
    Returns {delete: [ids in recipe order], dependents: [ids added because they use a deleted step],
    kept: [ids kept because live steps use them]}."""
    from .recipe import references  # noqa: PLC0415

    steps = project.recipe().steps
    known = {s.id for s in steps}
    refs = {s.id: references(s, known) for s in steps}
    want = set(ids) & known
    kept: set[str] = set()
    if only_unused:
        live_refs = set().union(*[refs[s.id] for s in steps if s.id not in want and not s.stale] or [set()])
        changed = True
        while changed:                       # a kept step keeps what it uses
            changed = False
            for i in list(want):
                if i in live_refs or any(i in refs[k] for k in kept):
                    want.discard(i)
                    kept.add(i)
                    changed = True
    dep: set[str] = set()
    changed = True
    while changed and not only_unused:
        changed = False
        for s in steps:
            if s.id not in want and refs[s.id] & want:
                want.add(s.id)
                dep.add(s.id)
                changed = True
    order = [s.id for s in steps]
    return {"delete": [i for i in order if i in want], "dependents": [i for i in order if i in dep],
            "kept": [i for i in order if i in kept]}


def delete_steps(project: Project, ids: list[str] | set[str], label: str, only_unused: bool = False) -> dict:
    """Remove steps from the recipe as ONE undo entry (Ctrl+Z / Undo brings them back). Output files stay in
    outputs/ (undo needs them); plan_delete() decides the dependents."""
    plan = plan_delete(project, ids, only_unused)
    if not plan["delete"]:
        return plan
    before = history.snapshot(project)
    r = project.recipe()
    gone = set(plan["delete"])
    r.steps = [s for s in r.steps if s.id not in gone]
    project.save_recipe(r)
    history.commit(project, f"{label} ({len(gone)} step{'s' if len(gone) != 1 else ''})", before)
    _release_steps(project, gone)
    return plan


def delete_step(project: Project, step_id: str, dependents: str = "delete") -> dict:
    """Delete one step (History right-click, fix 3.13). Later steps that use it are deleted too (dependents='delete')
    or kept and marked stale (dependents='stale'). One undo entry. Returns plan_delete's dict + {stale: [ids]}."""
    if dependents not in ("delete", "stale"):
        raise ValueError("dependents must be 'delete' or 'stale'")
    plan = plan_delete(project, [step_id])
    if dependents == "delete":
        res = delete_steps(project, plan["delete"], f"delete step {step_id}")
        return {**res, "stale": []}
    before = history.snapshot(project)
    r = project.recipe()
    deps = set(plan["dependents"])
    r.steps = [s for s in r.steps if s.id != step_id]
    for s in r.steps:
        if s.id in deps:
            s.stale = True
    project.save_recipe(r)
    history.commit(project, f"delete step {step_id} ({len(deps)} later step(s) stale)", before)
    _release_steps(project, {step_id} | deps)
    return {"delete": [step_id], "dependents": [], "kept": [], "stale": [i for i in plan["dependents"]]}


def add_default_cleanup(project: Project, map_id: str, backend: str | None = None) -> list[RecipeStep]:
    """'Add default cleanup': the preset of the map's input state (raw / OIM-cleaned) as separate ebsd.clean steps in
    the default order (base.DEFAULT_CLEANUP), each on the output of the previous one; ONE undo entry."""
    from .backends.base import DEFAULT_CLEANUP  # noqa: PLC0415

    state = project.sample(map_id).input_state
    out = []
    with history.group(project, f"default cleanup {map_id} ({state})"):
        for operation, prm in DEFAULT_CLEANUP[state]:
            out.append(run(project, "ebsd.clean", {"map_id": map_id, "operation": operation, "params": prm}, backend))
    return out


def undo(project: Project) -> str | None:
    """Undo the last recorded action (recipe step or backend switch); returns its label."""
    return history.undo(project)


def redo(project: Project) -> str | None:
    return history.redo(project)


# ---- replay -------------------------------------------------------------------------------------
class OutputCheck(BaseModel):
    step_id: str
    op: str
    output: str
    status: str                 # identical | within_tolerance | not_compared | other_backend | different | missing
    detail: str = ""


class StepRun(BaseModel):
    step_id: str
    op: str
    map_id: str | None = None
    backend: str | None = None
    seconds: float = 0.0


class ReplayCancelled(Exception):
    """Raised by a should_stop callback / cancel request between two steps of run_recipe."""


class RunReport(BaseModel):
    """Result of running a recipe into a project (ti64 run, MCP run_recipe) or of a replay."""
    project_root: str
    backend_override: str | None
    cancelled: bool = False
    seconds: float = 0.0
    steps: list[StepRun] = []
    skipped: list[str] = []      # stale steps not run
    checks: list[OutputCheck] = []
    input_changes: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.input_changes and all(c.status in ("identical", "within_tolerance", "not_compared",
                                                           "other_backend") for c in self.checks)

    @property
    def replay_root(self) -> str:
        return self.project_root


ReplayReport = RunReport


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


def _compare_tables(a: Path, b: Path) -> tuple[bool, str]:
    """CSV / XLSX tables: same sheets, columns and shapes; numbers within the npz float tolerance."""
    import pandas as pd  # noqa: PLC0415

    read = (lambda f: {"": pd.read_csv(f)}) if a.suffix == ".csv" else (lambda f: pd.read_excel(f, sheet_name=None))
    ta, tb = read(a), read(b)
    if list(ta) != list(tb):
        return False, f"sheets {list(ta)} vs {list(tb)}"
    for sh in ta:
        x, y = ta[sh], tb[sh]
        if list(x.columns) != list(y.columns) or x.shape != y.shape:
            return False, f"{sh}: columns/shape differ"
        for c in x.columns:
            u, v = x[c].to_numpy(), y[c].to_numpy()
            if np.issubdtype(u.dtype, np.number) and np.issubdtype(v.dtype, np.number):
                if not np.allclose(u.astype(float), v.astype(float), rtol=1e-6, atol=1e-5, equal_nan=True):
                    return False, f"{sh}/{c}: max |diff| {np.nanmax(np.abs(u.astype(float) - v.astype(float))):.3g}"
            elif not (pd.Series(u).fillna("").astype(str).to_numpy() == pd.Series(v).fillna("").astype(str).to_numpy()).all():
                return False, f"{sh}/{c}: values differ"
    return True, ""


def load_recipe_file(path: str | Path) -> list[RecipeStep]:
    """Steps of a recipe file: a project's recipe.json ({"steps": [...]}) or a bare list of steps. A hand-written
    step needs only op and params; id, backend and recorded outputs are optional."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = data["steps"] if isinstance(data, dict) else data
    if not isinstance(steps, list):
        raise ValueError(f"{path}: expected a recipe ({{'steps': [...]}}) or a list of steps")
    return [RecipeStep.model_validate(st) for st in steps]


def _check_output(name: str, ref: OutputRef, new: RecipeStep, s: RecipeStep, recorded_root: Path | None,
                  root: Path) -> OutputCheck:
    nref = new.outputs.get(name)
    c = dict(step_id=s.id, op=s.op, output=name)
    if nref is None:
        return OutputCheck(**c, status="missing")
    if nref.content_hash == ref.content_hash:
        return OutputCheck(**c, status="identical")
    if s.backend and new.backend and s.backend != new.backend:
        # another engine never reproduces the bytes; comparing 2.6 M-point arrays value by value took ~13 s per
        # output and says nothing (backends agree within the conformance tolerances, tests/conformance)
        return OutputCheck(**c, status="other_backend",
                           detail=f"recorded with {s.backend}, replayed with {new.backend}: not compared value by value")
    if ref.kind in ("svg", "pdf"):
        return OutputCheck(**c, status="not_compared",
                           detail="vector file regenerated (renderer timestamps); its sidecar is compared")
    old = recorded_root / ref.path if recorded_root else None
    if old is None or not old.is_file():
        return OutputCheck(**c, status="different", detail="content hash differs (recorded file not available)")
    if ref.kind in ("npz", "csv", "xlsx"):
        cmp = _compare_npz if ref.kind == "npz" else _compare_tables
        ok, detail = cmp(old, root / nref.path)
        return OutputCheck(**c, status="within_tolerance" if ok else "different", detail=detail)
    if ref.kind == "json":
        a = json.loads(old.read_text(encoding="utf-8"))
        b = json.loads((root / nref.path).read_text(encoding="utf-8"))
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)) if isinstance(a, dict) else []
        return OutputCheck(**c, status="different", detail=f"keys: {diff}")
    return OutputCheck(**c, status="different", detail="content hash differs")


def run_recipe(target: Project, steps: list[RecipeStep], backend: str | None = None,
               include_stale: bool = False, until: str | None = None, recorded_root: Path | None = None,
               compare: bool = True, on_step: Callable[[int, int, RecipeStep], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> RunReport:
    """Run recipe steps in order into `target` (each recorded there with its original id, so references between
    steps stay valid). Backend steps use the recorded backend (or the target project's backend for hand-written
    steps without one) unless `backend` overrides it. Recorded outputs (content hashes) are compared with the new
    ones; `recorded_root` = the project the steps came from (for tolerance checks of differing arrays / tables).
    Stale steps are skipped unless include_stale; `until` stops after that step id. on_step(k, n, step) is called
    before each step (progress); should_stop() is checked between steps (Cancel: the running step finishes, the
    report is returned with cancelled=True)."""
    known = {s.id for s in target.recipe().steps}
    todo = [s for s in steps if include_stale or not s.stale]
    clash = sorted(known & {s.id for s in todo})
    if clash:
        raise ValueError(f"step ids already in project {target.root.name}: {clash}; run into a new project")
    for s in todo:
        if s.op not in OPS:
            raise KeyError(f"step {s.id}: unknown operation {s.op!r}")
    rep = RunReport(project_root=str(target.root), backend_override=backend,
                    skipped=[s.id for s in steps if s.stale and not include_stale])
    from .recipe import references  # noqa: PLC0415

    ids = {s.id for s in todo}
    last_use = {}                             # step id -> index of the last step that uses its backend object
    for k, s in enumerate(todo):
        for r in references(s, ids):
            last_use[r] = k
    t_start = time.perf_counter()
    for k, s in enumerate(todo):
        if should_stop and should_stop():
            rep.cancelled = True
            break
        for inp in s.inputs:
            if isinstance(inp, FileInput):
                p = Path(inp.path)
                if not p.is_file():
                    rep.input_changes.append(f"{s.id}: input missing: {p}")
                elif file_fingerprint(p) != inp.fingerprint:
                    rep.input_changes.append(f"{s.id}: input changed: {p}")
        if on_step:
            on_step(k, len(todo), s)
        if s.op in EXPORT_OPS:                # old / hand-written recipes: exports go to the export log (fix 3.4)
            new, _e = export(target, s.op, s.params, backend=backend or s.backend)
            rep.steps.append(StepRun(step_id=new.id, op=new.op, map_id=new.params.get("map_id"), backend=new.backend,
                                     seconds=new.metrics.get("seconds", 0.0)))
            continue
        new = run(target, s.op, s.params, backend=backend or s.backend, step_id=s.id)
        rep.steps.append(StepRun(step_id=new.id, op=new.op, map_id=new.params.get("map_id"), backend=new.backend,
                                 seconds=new.metrics.get("seconds", 0.0)))
        if compare:
            rep.checks += [_check_output(n, ref, new, s, recorded_root, target.root) for n, ref in s.outputs.items()]
        # free backend objects no later step uses (a replay kept every map, grain set and reconstruction alive and
        # ran out of RAM); materialize() rebuilds one if it is needed after all
        _release_steps(target, [i for i in ids if last_use.get(i, -1) <= k])
        if until and s.id == until:
            break
    rep.seconds = round(time.perf_counter() - t_start, 2)
    return rep


def replay(project: Project, backend: str | None = None, into: Path | None = None,
           include_stale: bool = False, until: str | None = None,
           on_step: Callable[[int, int, RecipeStep], None] | None = None,
           should_stop: Callable[[], bool] | None = None) -> RunReport:
    """Re-run the recipe in a scratch project and compare every output with the recorded one.
    Backend steps use the recorded backend unless `backend` overrides it; `until` stops after that step."""
    root = into or project.cache_dir / f"replay_{datetime.now():%Y%m%d_%H%M%S}"
    if root.exists():
        shutil.rmtree(root)
    scratch = Project.create(root.name, base=root.parent, backend=project.info().backend)
    try:
        return run_recipe(scratch, project.recipe().steps, backend=backend, include_stale=include_stale, until=until,
                          recorded_root=project.root, on_step=on_step, should_stop=should_stop)
    finally:
        release_project(scratch)              # backend objects of the scratch copy (RAM, MATLAB workspace)


def release_project(project: Project) -> int:
    """Forget (and release in the backends) all backend objects of a project, e.g. a scratch project. Returns how many."""
    root = str(project.root)
    keys = [k for k in SESSION if k[0] == root]
    for k in keys:
        h = SESSION.pop(k)
        try:
            get_backend(k[2]).release(h)
        except Exception:  # noqa: BLE001  (already gone / engine restarted)
            pass
    return len(keys)


def _release_steps(project: Project, step_ids) -> int:
    """Release the backend objects of these steps of a project (if alive)."""
    want, n = set(step_ids), 0
    for key in [k for k in SESSION if k[0] == str(project.root) and k[1] in want]:
        h = SESSION.pop(key)
        n += 1
        try:
            get_backend(key[2]).release(h)
        except Exception:  # noqa: BLE001
            pass
    return n


def release_stale(project: Project) -> int:
    """Release the backend objects of stale steps (a newer step replaced them); they are rebuilt if ever needed."""
    stale = {s.id for s in project.recipe().steps if s.stale}
    return _release_steps(project, stale)


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
                    be, project.cache_dir / "rebuild", need_object=True)
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
            h = (npz_hash(p) if ref.kind == "npz" else png_hash(p) if ref.kind in ("png", "tiff")
                 else bytes_hash(p) if ref.kind in ("csv", "svg", "pdf", "mosaic") else xlsx_hash(p) if ref.kind == "xlsx"
                 else json_hash(json.loads(p.read_text(encoding="utf-8"))))
            if h != ref.content_hash:
                bad.append(f"{s.id}:{name} changed")
    return bad


def ops_catalog() -> dict[str, dict]:
    return {n: {"doc": s.doc, "uses_backend": s.uses_backend, "params": s.params.model_json_schema()}
            for n, s in OPS.items()}


from . import ops_ebsd  # noqa: E402,F401  (registers the EBSD operations)
from . import ops_recon  # noqa: E402,F401  (registers the reconstruction operations)
from . import ops_grainsize  # noqa: E402,F401  (registers the grain-size operations)
from . import ops_export  # noqa: E402,F401  (registers annotations, crop and export, phase 5)
from . import ops_compare  # noqa: E402,F401  (registers compare.backends, phase 6)
from . import ops_stitch  # noqa: E402,F401  (registers sem.stitch, phase 6.5)
