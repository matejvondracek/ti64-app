"""MCP server: tools are thin wrappers over the service layer (the same functions the GUI calls).

Run:  ti64 mcp   (or python -m ti64app.mcp_server; stdio transport)
Projects live in projects/<name>/; every tool that changes something is a recipe step in recipe.json.
Every service operation (service.OPS) has a dedicated tool (TOOL_FOR_OP, checked by the tests); run_operation runs
any operation by name with its pydantic params. Resources (ti64://...) expose settings, backends, the operation
catalog, and per project its summary, recipe, steps and sample sheets.
Units: um, deg, MPa; 4PB geometry in mm, load in N. Map frame x right, y down (EDAX setting 2).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, create_model

from . import __version__, service
from .annotations import Annotation
from .backends import backend_status, get_backend
from .backends.base import CleanupParams, ReconParams
from .layers import LayerParams
from .modules.stitching import StitchParams
from .ops_export import ExportParams
from .ops_grainsize import GrainSizeParams
from .project import Project, list_projects, projects_dir
from .sample import (BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file,
                     suggest_input_state)
from .settings import FigureStyle, load_settings, save_preferences

Backend = Literal["pymtex", "matlab"]

server = MCPServer(
    name="ti64app",
    version=__version__,
    instructions=(
        "EBSD/BSE analysis of EBM Ti-6Al-4V (alpha + retained beta) with two interchangeable backends "
        "(pymtex = MTEX for Python, default; matlab = MATLAB + MTEX 7.2). Work inside a project (create_project / "
        "projects). Link a map with set_sample_sheet (map file + input state raw / OIM-cleaned are enough to load "
        "it). The build direction, the specimen and any 4PB geometry must come from the user, never guessed; they are "
        "only needed by the steps that use them (IPF along the build direction, prior-beta length / width along BD, "
        "4PB), which say so when they are missing. Typical chain: set_sample_sheet -> load_map -> cleanup_map -> calc_grains -> reconstruct_beta -> "
        "grain_size -> export_map. Every analysis action is a recipe step (resource ti64://projects/{project}/recipe), "
        "undoable and replayable; exports are not recipe steps but go to an export log (export_log, export_again). compare_backends runs one step on both backends and shows numbers + both figures. "
        "Map frame: x right, y down (EDAX setting 2); build-direction angle from +x toward +y. Units: um, deg, MPa; "
        "4PB geometry in mm, load in N. Long steps on full maps take 10 s - 3 min."),
)


# ---- helpers ------------------------------------------------------------------------------------
def _project(name: str, create: bool = False) -> Project:
    if create:
        return Project.open_or_create(name)
    root = projects_dir() / name
    if not (root / "project.json").is_file():
        raise ValueError(f"no project {name!r}; existing: {list_projects()} (create_project makes one)")
    return Project.open(root)


def _step_summary(step) -> dict[str, Any]:
    return {"step_id": step.id, "op": step.op, "backend": step.backend, "versions": step.versions,
            "metrics": step.metrics, "outputs": {k: v.path for k, v in step.outputs.items()}}


def _json_out(p: Project, step, name: str) -> Any:
    return json.loads((p.root / step.outputs[name].path).read_text(encoding="utf-8"))


def _path(p: Project, step, name: str) -> str:
    return str(p.root / step.outputs[name].path)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _set(model: type[BaseModel], v: Any) -> dict[str, Any]:
    """Fields the caller set, from a typed input (MCP) or a plain dict (Python callers), validated by `model`."""
    if v is None:
        return {}
    m = v if isinstance(v, BaseModel) else model.model_validate(v)
    return m.model_dump(mode="json", exclude_unset=True)


def _options(model: type[BaseModel], name: str, exclude: set[str], doc: str) -> type[BaseModel]:
    """Same fields, types and defaults as `model` minus `exclude` (typed tool inputs without duplicating models).
    Only the fields the caller sets are passed on (model_dump(exclude_unset=True))."""
    fields = {k: (f.annotation, f) for k, f in model.model_fields.items() if k not in exclude}
    return create_model(name, __doc__=doc, **fields)


def _partial(model: type[BaseModel], name: str, doc: str) -> type[BaseModel]:
    """All fields of `model` optional (None = keep the preset / current value)."""
    fields = {k: (f.annotation | None, None) for k, f in model.model_fields.items()}
    return create_model(name, __doc__=doc, **fields)


def _clean_options() -> type[BaseModel]:
    """Parameters of the single cleanup operations (base.CLEAN_OPS; field names are unique across operations), all
    optional: only those of the chosen operation are used, missing ones take their defaults."""
    from .backends.base import CLEAN_OPS  # noqa: PLC0415

    fields = {k: (f.annotation | None, None) for m in CLEAN_OPS.values() for k, f in m.model_fields.items()}
    return create_model("CleanStepOptions", __doc__="Parameters of the cleanup operation (deg, px, CI 0-1); only the "
                        "chosen operation's fields are used, the rest are ignored.", **fields)


CleanStepOptions = _clean_options()
CleanupOverrides = _partial(CleanupParams, "CleanupOverrides",
                            "Cleanup fields to change from the input-state preset (deg, px, CI 0-1).")
GrainSizeOptions = _options(GrainSizeParams, "GrainSizeOptions", {"map_id"},
                            "Grain-size parameters (um, deg, px); defaults as listed.")
ExportOptions = _options(ExportParams, "ExportOptions",
                         {"map_id", "layer", "overlays", "mode", "scale", "width_mm", "dpi", "format", "roi", "roi_id"},
                         "Further export options: annotations, roi_outlines, scale_bar, title, colorbar, legend, "
                         "line_px, background, name.")
FigurePatch = _partial(FigureStyle, "FigurePatch", "Figure settings to change (None = keep).")
StitchOptions = _options(StitchParams, "StitchOptions",
                         {"folder", "files", "detector", "se_layout", "info_strip", "blend", "exclude", "nudge",
                          "use_stage"},
                         "Further stitching options: strip_px, manual_layout, locked, refine_radius, seam, blend_band_px, "
                         "stage_map, search_um, min_overlap_px, "
                         "min_score, se_min_score, se_search_px, max_residual_px, rotation_check, normalize, "
                         "single_column_tol_px, tiff_tile, pyramid_min_px, preview_max_px, band_px.")

# every service operation and the tool that runs it (tests check that this covers service.OPS)
TOOL_FOR_OP = {
    "sample.set": "set_sample_sheet", "ebsd.load": "load_map", "up2.header": "read_up2_header",
    "bse.load": "load_bse", "ebsd.cleanup": "cleanup_map", "ebsd.clean": "clean_map_step", "grains.calc": "calc_grains",
    "map.compute": "compute_map", "figure": "make_figure", "compare.oim": "compare_with_oim",
    "recon.run": "reconstruct_beta", "recon.figure": "recon_figure", "recon.sweep": "recon_sweep",
    "grainsize.run": "grain_size", "grainsize.figure": "grain_size_figure",
    "grainsize.compare_oim": "compare_oim_grain_size", "annotations.set": "set_annotations",
    "ebsd.crop": "crop_map", "export.map": "export_map", "compare.backends": "compare_backends",
    "sem.stitch": "stitch_images", "sem.export": "export_mosaic", "texture.run": "texture",
    "texture.export_odf": "export_odf", "elastic.run": "elastic_properties",
}


# ---- backends / settings ---------------------------------------------------------------------
@server.tool()
def list_backends() -> dict[str, str]:
    """Analysis backends and whether they can run here ('available' or the reason they cannot)."""
    return {k: (v or "available") for k, v in backend_status().items()}


@server.tool()
def get_settings() -> dict[str, Any]:
    """Current user settings: default backend and shared figure style (machine paths not shown)."""
    return load_settings().model_dump(mode="json", include={"backend", "figure"})


@server.tool()
def set_figure_settings(figure: FigurePatch) -> dict[str, Any]:  # type: ignore[valid-type]
    """Change the shared figure style used by both renderers: ipf {phase: {key ipfHKLKey|ipfTSLKey|ipfHSVKey|
    ipfSpotKey, direction X|Y|Z|build|custom, custom_vector, key_in_figure}}, colormap_scalar (parula), colormap_kam
    (turbo), boundary_line_width (1.0 pt), boundary_color (k), scale_bar (true), width_cm (12), dpi (600)."""
    s = load_settings()
    s.figure = FigureStyle.model_validate({**s.figure.model_dump(), **_set(FigurePatch, figure)})
    save_preferences(s)
    return s.model_dump(mode="json", include={"figure"})


@server.tool()
def set_backend(backend: Backend, project: str | None = None) -> dict[str, Any]:
    """Set the default backend; with `project`, also switch that project (marks its results stale)."""
    reason = get_backend(backend).unavailable_reason()
    if reason:
        return {"ok": False, "error": f"{backend} unavailable: {reason}"}
    s = load_settings()
    s.backend = backend
    save_preferences(s)
    out: dict[str, Any] = {"ok": True, "default_backend": backend}
    if project:
        out["stale_steps"] = service.switch_backend(_project(project), backend)
    return out


@server.tool()
def list_operations() -> dict[str, dict]:
    """Every service operation: description, whether it uses a backend, JSON schema of its params, MCP tool."""
    return {n: {**c, "tool": TOOL_FOR_OP.get(n)} for n, c in service.ops_catalog().items()}


@server.tool()
def run_operation(project: str, op: str, params: dict[str, Any], backend: Backend | None = None) -> dict[str, Any]:
    """Run any service operation by name (see list_operations for names and param schemas); recorded like the
    dedicated tools. Prefer the dedicated tool when there is one."""
    p = _project(project, create=op == "sample.set")
    step = service.run(p, op, params, backend=backend)
    return {**_step_summary(step), "files": {k: str(p.root / r.path) for k, r in step.outputs.items()}}


# ---- projects / sample sheets ---------------------------------------------------------------
@server.tool()
def projects() -> list[str]:
    """Names of existing projects."""
    return list_projects()


@server.tool()
def create_project(name: str, backend: Backend | None = None) -> dict[str, Any]:
    """Create projects/<name>/ (backend defaults to the Settings backend)."""
    p = Project.create(name, backend=backend or load_settings().backend)
    return p.summary()


@server.tool()
def project_summary(project: str) -> dict[str, Any]:
    """Backend, maps with sample sheets and recipe steps of a project."""
    return _project(project).summary()


@server.tool()
def get_step(project: str, step_id: str) -> dict[str, Any]:
    """One recipe step: params, inputs, outputs (absolute paths), versions, metrics; small JSON outputs inline."""
    p = _project(project)
    s = p.recipe().step(step_id)
    out = s.model_dump(mode="json")
    out["files"] = {k: str(p.root / r.path) for k, r in s.outputs.items()}
    out["json"] = {k: json.loads((p.root / r.path).read_text(encoding="utf-8")) for k, r in s.outputs.items()
                   if r.kind == "json" and (p.root / r.path).is_file() and (p.root / r.path).stat().st_size < 50_000}
    return out


@server.tool()
def suggest_sample_defaults(map_file: str) -> dict[str, str]:
    """Suggested map_id and input state for a map file (the user must confirm the input state)."""
    return {"map_id": map_id_from_file(map_file), "input_state": suggest_input_state(map_file)}


def _axes(mode, labels, build_axis):
    from .sample import MapAxes  # noqa: PLC0415

    if mode is None and labels is None and build_axis is None:
        return None
    lab = list(labels or ["x", "y", "z"])
    if len(lab) != 3:
        raise ValueError("axis_labels: give three names (horizontal x, vertical y, normal z)")
    return MapAxes(mode=mode or "approximate", x_label=lab[0], y_label=lab[1], z_label=lab[2], build_axis=build_axis)


@server.tool()
def map_directions(project: str, map_id: str) -> dict[str, Any]:
    """Directions that texture (ipf_dirs) and elastic_properties (directions) accept for a map, named by the map
    sheet's axis labels / specimen axes: available ones with their vector in the result frame, unavailable ones with
    what the user has to enter, the defaults, and the frame (axis labels, exact / approximate)."""
    from . import texture as tx  # noqa: PLC0415

    p = _project(project)
    fr = tx.frame_for(p, map_id)
    return {"map_id": map_id, "frame": fr.as_dict(), "defaults": fr.defaults,
            "choices": tx.direction_choices(p, map_id)}


@server.tool()
def set_sample_sheet(
    project: str,
    map_file: str,
    input_state: Literal["raw", "oim_cleaned"],
    specimen_id: str | None = None,
    build_direction_angle_deg: float | None = None,
    build_direction_vector: list[float] | None = None,
    map_id: str | None = None,
    outer_span_mm: float | None = None,
    inner_span_mm: float | None = None,
    width_mm: float | None = None,
    thickness_mm: float | None = None,
    load_N: float | None = None,
    origin_X_mm: float | None = None,
    origin_Y_mm: float | None = None,
    origin_Z_mm: float | None = None,
    map_x_axis: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] | None = None,
    map_y_axis: Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"] | None = None,
    notes: str = "",
    phase_map: dict[str, Literal["alpha", "beta", "other"]] | None = None,
    axes_mode: Literal["exact", "approximate"] | None = None,
    axis_labels: list[str] | None = None,
    build_axis: Literal["x", "y", "z"] | None = None,
) -> dict[str, Any]:
    """Enter the sample sheet of one map (creates the project if needed). Only map_file and input_state are needed
    to load the map. The build direction (angle in deg from map +x toward +y, or a 3-vector in map coordinates) and
    the specimen must be given by the user, never guessed; leave them out until known (steps that need them say so). 4PB: give all
    four geometry values (mm) or none; load in N. Position: map origin in the sample frame (mm) and the sample
    axes along map +x / +y. A relative map_file is resolved against the server's working directory.
    Phases are found by crystal symmetry (6/mmm -> 'Ti (alpha)', m-3m -> 'Ti (beta)'), never by name; phase_map
    ({file phase id: 'alpha' | 'beta' | 'other'}) is only needed when the symmetry does not decide (loading then says
    so) and must come from the user.
    Map axes (fix 6.7.1): axes_mode 'exact' (default: BD + position define the specimen frame) or 'approximate':
    axis_labels = names of the map's horizontal (x), vertical (y) and normal (z, beam) axes, e.g. ['L', 'S', 'T'], and
    build_axis = which of them is the build direction. The labels name the directions in texture / elastic results."""
    bend = None
    geo = (outer_span_mm, inner_span_mm, width_mm, thickness_mm)
    if any(v is not None for v in geo):
        if any(v is None for v in geo):
            raise ValueError("4PB geometry: give outer_span_mm, inner_span_mm, width_mm and thickness_mm")
        bend = FourPointBending(outer_span_mm=outer_span_mm, inner_span_mm=inner_span_mm,
                                width_mm=width_mm, thickness_mm=thickness_mm, load_N=load_N)
    pos = None
    if any(v is not None for v in (origin_X_mm, origin_Y_mm, origin_Z_mm, map_x_axis, map_y_axis)):
        pos = MapPosition(**_clean(dict(origin_X_mm=origin_X_mm, origin_Y_mm=origin_Y_mm, origin_Z_mm=origin_Z_mm,
                                        map_x_axis=map_x_axis, map_y_axis=map_y_axis)))
    bd = (BuildDirection(angle_deg=build_direction_angle_deg,
                         vector=tuple(build_direction_vector) if build_direction_vector else None)
          if build_direction_angle_deg is not None or build_direction_vector else None)
    sheet = SampleSheet(map_id=map_id or map_id_from_file(map_file), specimen_id=specimen_id,
                        map_file=map_file, input_state=input_state, build_direction=bd,
                        bending=bend, position=pos, notes=notes, phase_map=phase_map, axes=_axes(
                            axes_mode, axis_labels, build_axis))
    step = service.run(_project(project, create=True), "sample.set", sheet)
    return {**_step_summary(step), "map_id": sheet.map_id, "build_direction_unit": bd.unit_vector if bd else None}


@server.tool()
def get_sample_sheet(project: str, map_id: str) -> dict[str, Any]:
    """The stored sample sheet of a map, plus map info: indexing method label ('Hough' = microscope default,
    'Spherical' = re-indexed in OIM Analysis, null = no label) and the reference frame of its newest load (the
    .ang's CartesianAlignment, the crystal frame the backend used, which OIM axis each app axis is)."""
    import json as _json  # noqa: PLC0415

    from .sample import indexing_label  # noqa: PLC0415

    p = _project(project)
    out = p.sample(map_id).model_dump(mode="json")
    m = p.file(map_id)
    out["indexing"] = indexing_label(m)
    load = service.latest_step(p, ("ebsd.load",), map_id=map_id)
    if load is not None and "frame" in load.outputs:
        out["frame"] = _json.loads((p.root / load.outputs["frame"].path).read_text(encoding="utf-8"))
    return out


@server.tool()
def set_indexing_label(project: str, map_id: str, indexing: Literal["hough", "spherical"] | None) -> dict[str, Any]:
    """Label an EBSD map with how it was indexed: 'hough' (the microscope's default), 'spherical' (re-indexed with
    spherical indexing in OIM Analysis) or null (no label). Metadata only: no result becomes stale."""
    from .sample import indexing_label  # noqa: PLC0415

    m = _project(project).update_file(map_id, indexing=indexing, indexing_asked=True)
    return {"map_id": map_id, "indexing": indexing_label(m)}


# ---- loaders --------------------------------------------------------------------------------
@server.tool()
def load_map(project: str, map_id: str, backend: Backend | None = None) -> dict[str, Any]:
    """Load a map's .ang (EDAX setting 2) with the project backend or `backend`. The map must be linked in the
    project (set_sample_sheet with map_file + input_state); sample and build direction are not needed to load."""
    p = _project(project)
    step = service.apply(p, "ebsd.load", {"map_id": map_id}, backend=backend)
    return {**_step_summary(step), "meta": _json_out(p, step, "meta"), "frame": _json_out(p, step, "frame")}


@server.tool()
def read_up2_header(project: str, file: str) -> dict[str, Any]:
    """Header of an EDAX .up2 pattern file (patterns are not loaded): grid, step, scan points vs patterns in the file
    (hex files with the 'extra patterns' flag hold one padding pattern per short row, skipped by the app), the scan ID
    and the paired .ang with the same '# SCANID' (project files first, then the .up2's folder)."""
    from .modules.up2_pair import paired  # noqa: PLC0415

    p = _project(project)
    step = service.run(p, "up2.header", {"file": file})
    pr = paired(p, file)
    return {**_step_summary(step), "header": _json_out(p, step, "header"),
            "paired_ang": ({"file": str(pr.ang), "map_id": pr.map_id} if pr else None)}


@server.tool()
def up2_overview(project: str, file: str, step: int = 2, channel: Literal["iq", "vbse"] = "iq",
                 flatten: bool = True) -> dict[str, Any]:
    """Overview map of a .up2 pattern file (pattern quality IQ or virtual BSE), computed by streaming the file in
    chunks (every `step`-th scan row / column; 2 = half the file read, ~1 min for 43 GB; 1 = all patterns) and cached
    in the project's cache folder (same cache as the View tab). Returns the PNG path, the grid and value ranges.
    A viewer cache, not a recipe step (like the viewer tiles)."""
    from PIL import Image  # noqa: PLC0415

    from .modules import up2_overview as uo  # noqa: PLC0415

    p = _project(project)
    ov = uo.compute(file, p.cache_dir, step)
    png = uo.cache_path(p.cache_dir, file, step).with_name(
        uo.cache_path(p.cache_dir, file, step).stem + f"_{channel}{'_flat' if flatten else ''}.png")
    Image.fromarray(ov.image(channel, flatten=flatten)).save(png)
    import numpy as np  # noqa: PLC0415

    a = ov.channel(channel)
    return {"png": str(png), "shape_rows_cols": list(a.shape), "pixel_um": list(ov.pixel_um), "step": ov.step,
            "seconds": round(ov.seconds, 1), "channel": uo.CHANNELS[channel],
            "percentiles_1_50_99": [float(v) for v in np.nanpercentile(a, (1, 50, 99))]}


@server.tool()
def load_bse(project: str, image: str, txt: str | None = None) -> dict[str, Any]:
    """Load a JEOL BSE image and its .txt metadata (stage X/Y/Z/tilt/rotation, WD, kV, mag, pixel size)."""
    p = _project(project)
    step = service.run(p, "bse.load", {"image": image, "txt": txt})
    return {**_step_summary(step), "metadata": _json_out(p, step, "meta")}


# ---- EBSD -----------------------------------------------------------------------------------
@server.tool()
def cleanup_map(project: str, map_id: str, overrides: CleanupOverrides | None = None,  # type: ignore[valid-type]
                source: str | None = None, backend: Backend | None = None) -> dict[str, Any]:
    """Cleanup with the preset of the map's input state (raw: CI < 0.1 removed; OIM-cleaned: no CI filter; both:
    EBSD_part2 noise removal at noise_angle_deg 3, indexed grains <= 2 px removed, voids <= 4 px refilled).
    overrides change single preset fields. source = load step (default newest). The loaded map is not changed."""
    p = _project(project)
    ov = _set(CleanupOverrides, overrides)
    step = service.apply(p, "ebsd.cleanup", _clean({"map_id": map_id, "overrides": ov, "source": source}),
                       backend=backend)
    return {**_step_summary(step), "report": _json_out(p, step, "report")}


@server.tool()
def clean_map_step(project: str, map_id: str,
                   operation: Literal["ci_threshold", "spike_removal", "noise_removal", "void_fill", "grain_dilation",
                                      "grain_ci", "smoothing"],
                   params: CleanStepOptions | None = None, source: str | None = None,  # type: ignore[valid-type]
                   backend: Backend | None = None) -> dict[str, Any]:
    """ONE cleanup operation as its own recipe step, on the current map (the output of the previous load / cleanup
    step of the map, or `source`), so the order is free. Parameters (defaults):
    ci_threshold {ci_threshold 0.1}; spike_removal {spike_angle_deg 5}; noise_removal {noise_angle_deg 3,
    noise_max_indexed_px 2, fill true}; void_fill {max_void_px 4, angle_deg 3}; grain_dilation (OIM style)
    {tolerance_deg 5, min_grain_px 2, multi_row false, single_iteration true}; grain_ci (OIM 'Grain CI
    Standardization': every point of a grain gets the grain's mean or max CI, orientations unchanged; usually after
    grain dilation) {tolerance_deg 5, min_grain_px 2, mode 'mean' | 'max'}; smoothing {hq_alpha 0.5,
    hq_threshold_deg 15}. Applying the same operation again right after itself replaces that step (tuning).
    Report: removed / filled / unfilled points and why; grain dilation: points changed per iteration."""
    from .backends.base import CLEAN_OPS  # noqa: PLC0415

    p = _project(project)
    given = _set(CleanStepOptions, params)
    prm = {k: v for k, v in given.items() if k in CLEAN_OPS[operation].model_fields}
    step = service.apply(p, "ebsd.clean", _clean({"map_id": map_id, "operation": operation, "params": prm,
                                                   "source": source}), backend=backend)
    return {**_step_summary(step), "report": _json_out(p, step, "report")}


@server.tool()
def add_default_cleanup(project: str, map_id: str, backend: Backend | None = None) -> dict[str, Any]:
    """The cleanup preset of the map's input state as separate steps in the default order (raw: CI threshold 0.1,
    noise-grain removal, void fill; OIM-cleaned: noise-grain removal, void fill). One undo entry."""
    p = _project(project)
    steps = service.add_default_cleanup(p, map_id, backend)
    return {"steps": [{**_step_summary(s), "report": _json_out(p, s, "report")} for s in steps]}


@server.tool()
def texture(project: str, map_id: str, phase: Literal["alpha", "beta"] = "alpha",
            region: Literal["map", "roi", "sample"] = "map", roi_id: str | None = None,
            weighting: Literal["pixel", "grain"] = "pixel", halfwidth_deg: float = 5.0,
            poles: list[str] | None = None, ipf_dirs: list[str] | None = None,
            custom_vector: tuple[float, float, float] | None = None,
            plot: Literal["density", "scatter", "both"] = "density", backend: Backend | None = None) -> dict[str, Any]:
    """Texture (phase 6.7) of alpha or reconstructed prior-beta ('beta') orientations of a map, an ROI or all maps of
    its sample, in the specimen frame (X beam axis, Y thickness, Z width, from the map sheet position; BD from the
    sample sheet): kernel-density ODF (halfwidth deg), max MRD, texture index, grains used (warns when few), pole
    figures (alpha {0001} {10-10} {11-20} {10-11}; beta {100} {110} {111}) and inverse pole figures drawn by the
    backend. weighting: per pixel (area) or one point per grain. ipf_dirs: names from map_directions (the map axes by
    the map sheet's labels, BD, beam axis / thickness / width when the position is entered, 'custom'); default = the
    map's default directions. Unavailable directions are skipped with a note, never an error."""
    p = _project(project)
    step = service.apply(p, "texture.run", _clean({
        "map_id": map_id, "phase": phase, "region": region, "roi_id": roi_id, "weighting": weighting,
        "halfwidth_deg": halfwidth_deg, "poles": poles, "ipf_dirs": ipf_dirs, "custom_vector": custom_vector,
        "plot": plot}), backend=backend)
    return {**_step_summary(step), "summary": _json_out(p, step, "summary"), "pole_figures": _path(p, step, "pf"),
            "inverse_pole_figures": _path(p, step, "ipf")}


@server.tool()
def export_odf(project: str, texture_step: str, file: str | None = None) -> dict[str, Any]:
    """Export the ODF of a texture step in MTEX's text format (loadODF), with a JSON sidecar; logged in the export
    log. file = target path (default: only inside the project's exports folder)."""
    p = _project(project)
    step, e = service.export(p, "texture.export_odf", {"texture": texture_step}, target=file)
    return {"export_id": e.id, "file": e.file or str(p.root / step.outputs["export"].path)}


@server.tool()
def elastic_properties(project: str, map_id: str, region: Literal["map", "roi", "sample"] = "map",
                       roi_id: str | None = None, weighting: Literal["pixel", "grain"] = "pixel",
                       preset_alpha: str | None = None, preset_beta: str | None = None, include_beta: bool = True,
                       directions: list[str] | None = None, custom_vector: tuple[float, float, float] | None = None,
                       bootstrap_runs: int = 200, backend: Backend | None = None) -> dict[str, Any]:
    """Young's modulus from texture (phase 6.7): single-crystal constants (Settings presets, GPa; list them with
    elastic_presets) rotated into the specimen frame and averaged: Voigt / Reuss / Hill 6x6 matrices, E along the
    directions (names from map_directions, e.g. the user's axis labels L / S / T; default = the map's defaults) with
    +- SD from a bootstrap over grains, Emax / Emin, stereographic + 3D plot. Alpha + retained beta mixed by area
    fraction (a map without beta: alpha only); porosity ignored."""
    p = _project(project)
    step = service.apply(p, "elastic.run", _clean({
        "map_id": map_id, "region": region, "roi_id": roi_id, "weighting": weighting, "preset_alpha": preset_alpha,
        "preset_beta": preset_beta, "include_beta": include_beta, "directions": directions,
        "custom_vector": custom_vector, "bootstrap_runs": bootstrap_runs}), backend=backend)
    return {**_step_summary(step), "summary": _json_out(p, step, "summary"), "figure": _path(p, step, "figure")}


@server.tool()
def sample_youngs_modulus(project: str, sample_id: str, direction: str = "beam axis") -> dict[str, Any]:
    """E along one direction (default the beam axis) for every EBSD map of a sample, from each map's newest elastic
    result (input for the 4PB stress maps of phase 7). Maps without a result have E null."""
    from .ops_texture import sample_E_table  # noqa: PLC0415

    return {"sample": sample_id, "direction": direction, "maps": sample_E_table(_project(project), sample_id, direction)}


@server.tool()
def elastic_presets() -> dict[str, Any]:
    """Single-crystal elastic-constant presets (GPa) stored in Settings, with source and DOI, and the defaults."""
    from .settings import load_settings  # noqa: PLC0415

    es = load_settings().elastic
    return {"default_alpha": es.default_alpha, "default_beta": es.default_beta,
            "presets": {k: v.model_dump(mode="json") for k, v in es.presets.items()}}


@server.tool()
def calc_grains(project: str, map_id: str, hagb_deg: float = 15.0, lagb_deg: float = 2.0, min_px: int = 1,
                alpha: float = 3.1, burgers_tol_deg: float = 3.0, source: str | None = None,
                backend: Backend | None = None) -> dict[str, Any]:
    """Alpha grains bounded by HAGB >= hagb_deg (LAGB lagb_deg..hagb_deg inside grains) on the newest cleaned map
    (or `source`); boundary lengths (um) per class: LAGB, HAGB, alpha/beta interface, Burgers types 10.53 / 60 /
    60.83 / 63.26 / 90 deg within burgers_tol_deg. alpha = calcGrains smoothing (3.1), min_px per grain (1)."""
    p = _project(project)
    step = service.apply(p, "grains.calc", _clean({
        "map_id": map_id, "source": source,
        "grains": {"hagb_deg": hagb_deg, "lagb_deg": lagb_deg, "min_px": min_px, "alpha": alpha},
        "boundaries": {"burgers_tol_deg": burgers_tol_deg}}), backend=backend)
    return {**_step_summary(step), "summary": _json_out(p, step, "summary")}


@server.tool()
def compute_map(project: str, map_id: str, kind: Literal["kam", "grod", "gos", "gnd"], kam_order: int = 1,
                kam_threshold_deg: float = 5.0, gnd_stencil: Literal["1hop", "oneSided", "full"] = "1hop",
                grains: str | None = None, backend: Backend | None = None) -> dict[str, Any]:
    """Per-pixel KAM (kernel order 1-3, cutoff kam_threshold_deg 5), GROD, GOS (deg) or GND (1/m^2, alpha:
    basal/prismatic/pyramidal <a> + pyramidal <c+a>) inside the newest grains of the map (or `grains` step)."""
    p = _project(project)
    step = service.apply(p, "map.compute", _clean({"map_id": map_id, "kind": kind, "kam_order": kam_order,
                                                 "kam_threshold_deg": kam_threshold_deg, "gnd_stencil": gnd_stencil,
                                                 "grains": grains}), backend=backend)
    return {**_step_summary(step), "stats": _json_out(p, step, "stats")}


@server.tool()
def make_figure(project: str, map_id: str,
                kind: Literal["ipf", "phase", "ci", "iq", "fit", "prias", "prias_top", "prias_center", "prias_bottom", "kam", "grod", "gos", "gnd",
                              "boundaries", "burgers", "ipf_key"],
                overlays: list[Literal["hagb", "lagb", "interface", "burgers", "beta_gb", "crack"]] | None = None,
                phases: list[str] | None = None,
                direction: Literal["X", "Y", "Z", "build", "custom"] | None = None,
                custom_vector: tuple[float, float, float] | None = None, key: str | None = None,
                clim: tuple[float, float] | None = None, source: str | None = None, grains: str | None = None,
                values: str | None = None, recon: str | None = None) -> dict[str, Any]:
    """Draw a map with the project backend; returns the PNG path. IPF key and direction default to Settings per
    phase ('build' = build direction from the sample sheet). clim in the map's unit (deg, 1/m^2). Step references
    (source, grains, values, recon) default to the newest. Fit/PRIAS report clearly when the file lacks them; PRIAS
    comes from the <map>_prias.txt exports next to the .ang (prias = first channel, prias_top / _center / _bottom)."""
    p = _project(project)
    step = service.apply(p, "figure", _clean({
        "map_id": map_id, "kind": kind, "overlays": overlays or [], "phases": phases, "direction": direction,
        "custom_vector": custom_vector, "key": key, "clim": clim, "source": source, "grains": grains,
        "values": values, "recon": recon}))
    return {**_step_summary(step), "png": _path(p, step, "figure"), "meta": _json_out(p, step, "meta")}


@server.tool()
def compare_with_oim(project: str, map_id: str, image: str, kind: Literal["ipf", "ci"] = "ipf",
                     key: str = "ipfTSLKey", direction: Literal["X", "Y", "Z", "build"] = "Z") -> dict[str, Any]:
    """Compare our IPF/CI map (from the LOADED, not cleaned, data) with an OIM image; all 8
    flips/rotations are scored so a reference-frame mismatch is reported."""
    p = _project(project)
    step = service.run(p, "compare.oim", {"map_id": map_id, "image": image, "kind": kind, "key": key,
                                          "direction": direction})
    return {**_step_summary(step), "result": _json_out(p, step, "result")}


# ---- prior-beta reconstruction ----------------------------------------------------------------
@server.tool()
def reconstruct_beta(project: str, map_id: str, params: ReconParams | None = None, source: str | None = None,
                     min_px: int = 1, backend: Backend | None = None) -> dict[str, Any]:
    """Prior-beta reconstruction (EBSD_part2.m method; Niessen 2022 doi:10.1107/S1600576721011560, Hielscher 2022
    doi:10.1016/j.mtla.2022.101399) of the LOADED map (or `source` = a cleanup step). params (deg): method
    variantGraph, vgThreshold 5, vgTolerance 1.5, vgNumIter 3, vgInflation 1.05, segAngle 1.5, cleanAngle 3, orMode
    'limit' (refined OR used only if <= orMaxDev 1 deg from Burgers) | 'burgers' | 'refined', mergeMode 'single' |
    'progressive', mergeThreshold 3, inclusionMaxSize 5, histMinECD 5 um. Returns OR deviation / OR used, grains,
    reconstructed fraction, fit, ECD, merge sweep, variant / packet statistics, size along / across the BD."""
    p = _project(project)
    step = service.apply(p, "recon.run", _clean({"map_id": map_id, "source": source, "min_px": min_px,
                                               "params": _set(ReconParams, params)}),
                       backend=backend)
    return {**_step_summary(step), "summary": _json_out(p, step, "summary")}


def _backend_of(p: Project, op: str, map_id: str, step_id: str | None) -> str | None:
    s = p.recipe().step(step_id) if step_id else service.latest_step(p, (op,), map_id=map_id)
    return s.backend if s else None


@server.tool()
def recon_figure(project: str, map_id: str,
                 kind: Literal["parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                               "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist"],
                 recon: str | None = None, direction: Literal["X", "Y", "Z", "build", "custom"] | None = None,
                 key: str | None = None, clim: tuple[float, float] | None = None) -> dict[str, Any]:
    """Figure of the newest (or given) reconstruction, drawn by the backend that computed it: parent grain IPF map,
    alpha IPF with prior-beta boundaries, fit / variant / packet maps, boundary misorientation map, beta IPF key,
    histograms (neighbour misorientation, fit, ECD, variants per grain). Returns the PNG path."""
    p = _project(project)
    step = service.apply(p, "recon.figure", _clean({"map_id": map_id, "kind": kind, "recon": recon,
                                                  "direction": direction, "key": key, "clim": clim}),
                       backend=_backend_of(p, "recon.run", map_id, recon))
    return {**_step_summary(step), "png": _path(p, step, "figure"), "meta": _json_out(p, step, "meta")}


@server.tool()
def recon_sweep(project: str, map_id: str, grid: dict[str, list[float | int | str | bool]],
                base: ReconParams | None = None, thumbnails: bool = True, max_runs: int = 24,
                backend: Backend | None = None) -> dict[str, Any]:
    """Reconstruction-parameter sweep: every combination of `grid` (ReconParams names -> value lists, e.g.
    {"vgThreshold": [5, 8], "mergeThreshold": [0.5, 3, 6]}) on top of `base`. Per run: reconstructed fraction,
    parent-fit quantiles, beta grain count, area-weighted ECD, bridged steps, split noise, thumbnail PNG."""
    p = _project(project)
    params = {"map_id": map_id, "grid": grid, "thumbnails": thumbnails, "max_runs": max_runs,
              "base": {"sweep": [], **_set(ReconParams, base)}}
    step = service.run(p, "recon.sweep", params, backend=backend)
    return {**_step_summary(step), "table": _json_out(p, step, "table"),
            "thumbnails": [str(p.root / r.path) for k, r in sorted(step.outputs.items()) if k.startswith("thumb_")]}


# ---- grain size -----------------------------------------------------------------------------------
@server.tool()
def grain_size(project: str, map_id: str, params: GrainSizeOptions | None = None,  # type: ignore[valid-type]
               backend: Backend | None = None) -> dict[str, Any]:
    """Grain size per ISO 13067 / ASTM E2627 rules on the newest grains.calc partition (lath threshold = its
    hagb_deg, default 15 deg) and, if present, the newest reconstruction. params: min_px 5, exclude_edge true,
    lath_min_aspect 2, lath_angle_bin_deg 5, colony {burgers_tol_deg 3}, beta_line_spacing_um 1, beta_min_ecd_um
    (default recon histMinECD), use_recon true. Returns alpha ECD (number / area-weighted, um), lath thickness,
    colony ECD, prior-beta ECD, width / length vs build direction, retained-beta fraction, log-normal fits,
    warnings and the CSV/XLSX table paths."""
    p = _project(project)
    step = service.apply(p, "grainsize.run", {"map_id": map_id, **_set(GrainSizeOptions, params)},
                       backend=backend)
    return {**_step_summary(step), "summary": _json_out(p, step, "summary"),
            "tables": {k: str(p.root / r.path) for k, r in step.outputs.items() if r.kind in ("csv", "xlsx")}}


@server.tool()
def grain_size_figure(project: str, map_id: str,
                      kind: Literal["alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd",
                                    "colony_ecd_area", "beta_ecd", "beta_width", "beta_length", "colony_map"],
                      grainsize: str | None = None, bin_width_um: float | None = None,
                      min_um: float | None = None, max_um: float | None = None) -> dict[str, Any]:
    """Histogram with log-normal fit (or the colony map) of the newest (or given) grain-size step, drawn by the
    backend that computed it. bin_width_um = bin width (empty: automatic); min_um / max_um = range shown (the fit and
    the statistics stay those of all grains). Returns the PNG path."""
    p = _project(project)
    step = service.apply(p, "grainsize.figure", _clean({"map_id": map_id, "kind": kind, "grainsize": grainsize,
                                                      "bin_width_um": bin_width_um, "min_um": min_um,
                                                      "max_um": max_um}),
                       backend=_backend_of(p, "grainsize.run", map_id, grainsize))
    return {**_step_summary(step), "png": _path(p, step, "figure")}


@server.tool()
def compare_oim_grain_size(project: str, map_id: str, file: str, phase: int | None = None) -> dict[str, Any]:
    """Validate the alpha grain ECD of the newest grain-size step against an OIM Analysis grain file export
    (same min-pixel / edge rules applied to the OIM grains; means, log-normal fits, two-sample KS, histogram)."""
    p = _project(project)
    step = service.run(p, "grainsize.compare_oim", _clean({"map_id": map_id, "file": file, "phase": phase}),
                       backend=_backend_of(p, "grainsize.run", map_id, None))
    return {**_step_summary(step), "png": _path(p, step, "comparison"), "result": _json_out(p, step, "result")}


# ---- backends: compare -------------------------------------------------------------------------
@server.tool()
def compare_backends(project: str, op: str, params: dict[str, Any], figure: bool = True,
                     figure_kind: str | None = None, keep_scratch: bool = False) -> dict[str, Any]:
    """Run ONE backend operation (e.g. recon.run, ebsd.cleanup, grains.calc, map.compute, grainsize.run; params as
    for run_operation, incl. map_id) on pymtex AND matlab in a scratch copy of the project, and compare: every
    number of its JSON outputs side by side (abs / rel difference, values > 1 % flagged), run times, and both
    figures (each drawn by its own engine; quick-look kind via figure_kind, e.g. 'parent_ipf' / 'variant' for
    recon.run). Bit-identical results are not expected. The project's own recipe gets one compare step."""
    p = _project(project)
    step = service.run(p, "compare.backends", {"op": op, "params": params, "figure": figure,
                                               "figure_kind": figure_kind, "keep_scratch": keep_scratch})
    t = _json_out(p, step, "table")
    flagged = [r for r in t["rows"] if (r["rel_diff"] or 0) > 0.01 or (r["rel_diff"] is None and not r["same"])]
    return {**_step_summary(step), "table": str(p.root / step.outputs["table"].path),
            "versions": t["versions"], "n_values": t["n_values"], "n_identical": t["n_identical"],
            "max_rel_diff": t["max_rel_diff"], "differing_over_1pct_or_text": flagged[:60],
            "rows": t["rows"][:120], "performance": step.metrics,
            "figures": {k: str(p.root / r.path) for k, r in step.outputs.items() if r.kind == "png"}}


# ---- recipes ---------------------------------------------------------------------------------------
@server.tool()
def replay_project(project: str, backend: Backend | None = None, include_stale: bool = False) -> dict[str, Any]:
    """Re-run the project's recipe in a scratch copy and compare all outputs with the recorded ones."""
    rep = service.replay(_project(project), backend=backend, include_stale=include_stale)
    return {"ok": rep.ok, "replay_root": rep.replay_root, **rep.model_dump(mode="json")}


@server.tool()
def run_recipe(recipe_file: str, project: str, backend: Backend | None = None, include_stale: bool = False,
               compare: bool = True) -> dict[str, Any]:
    """Run a recipe file (a project's recipe.json or a list of {op, params[, backend]} steps) into `project` (created
    if missing; must not already contain the same step ids). Same as the CLI `ti64 run`. Recorded outputs are
    compared with the new ones when compare is true."""
    f = Path(recipe_file)
    steps = service.load_recipe_file(f)
    p = _project(project, create=True)
    rep = service.run_recipe(p, steps, backend=backend, include_stale=include_stale, compare=compare,
                             recorded_root=f.parent if (f.parent / "project.json").is_file() else None)
    return {"ok": rep.ok, **rep.model_dump(mode="json")}


@server.tool()
def verify_outputs(project: str) -> dict[str, Any]:
    """Check that every stored output file still has its recorded content hash."""
    bad = service.verify_outputs(_project(project))
    return {"ok": not bad, "problems": bad}


# ---- annotations, crop, export, undo / redo ---------------------------------------------------------
@server.tool()
def get_annotations(project: str, map_id: str) -> list[dict[str, Any]]:
    """Annotations and ROIs of a map (coordinates in um, map frame x right / y down)."""
    from .annotations import load_annotations  # noqa: PLC0415

    return [a.model_dump(mode="json") for a in load_annotations(_project(project), map_id)]


class AnnotationIn(BaseModel):
    """One annotation / ROI (um, map frame). type: text, arrow, line, polyline, point, crack, roi_rect, roi_poly."""
    id: str | None = None
    type: Annotation.model_fields["type"].annotation  # type: ignore[valid-type]
    xy: list[tuple[float, float]]
    text: str = ""
    color: str = ""
    width_pt: float = 1.5
    size_pt: float = 9.0
    export: bool = True


@server.tool()
def set_annotations(project: str, map_id: str, items: list[AnnotationIn]) -> dict[str, Any]:
    """Replace the annotations / ROIs of a map (recorded, undoable). Each item: type (text, arrow, line, polyline,
    point, crack, roi_rect, roi_poly), xy [[x, y], ...] in um, optional text (label / ROI name), color '#rrggbb',
    width_pt, size_pt, export. Crack paths are shown by the 'crack' overlay."""
    rows = [{**_clean(_set(AnnotationIn, it)), "map_id": map_id} for it in items]
    step = service.run(_project(project), "annotations.set", {"map_id": map_id, "items": rows})
    return _step_summary(step)


@server.tool()
def crop_map(project: str, map_id: str, new_map_id: str, roi: tuple[float, float, float, float] | None = None,
             roi_id: str | None = None) -> dict[str, Any]:
    """Crop a map to an ROI (x0, y0, x1, y1 in um, or the id / name of an ROI annotation): writes the .ang rows
    inside it to maps/<new_map_id>.ang in the project and a sample sheet copied from the parent. Then load_map it."""
    step = service.run(_project(project), "ebsd.crop",
                       _clean({"map_id": map_id, "new_map_id": new_map_id, "roi": roi, "roi_id": roi_id}))
    return _step_summary(step)


@server.tool()
def export_map(project: str, map_id: str, kind: str = "ipf",
               overlays: list[Literal["hagb", "lagb", "interface", "burgers", "beta_gb", "crack"]] | None = None,
               mode: Literal["native", "scale", "width"] = "native", scale: Literal[2, 4, 8] = 2,
               width_mm: float = 120.0, dpi: int = 600, format: Literal["png", "tiff", "svg", "pdf"] = "png",
               roi: tuple[float, float, float, float] | None = None, roi_id: str | None = None,
               layer: LayerParams | None = None, options: ExportOptions | None = None,  # type: ignore[valid-type]
               backend: Backend | None = None) -> dict[str, Any]:
    """Export a map layer (kind: ipf, phase, ci, iq, kam, grod, gos, gnd, parent_ipf, recon_fit, variant, packet)
    with overlays and the map's annotations. mode native = one pixel per measurement, scale = 2/4/8x nearest
    neighbour (PNG / TIFF, pixel size in the file and the sidecar); width = figure width_mm wide at dpi drawn by
    the backend (PNG / TIFF / SVG / PDF). roi x0, y0, x1, y1 in um. layer: source steps, direction, key, ipf_keys,
    clim. Recon layers must use the backend that computed the reconstruction (pass backend). Returns the file,
    the JSON sidecar and its content."""
    p = _project(project)
    lay = {**_set(LayerParams, layer), "kind": kind}
    params = {"map_id": map_id, "layer": lay, "overlays": overlays or [], "mode": mode, "scale": scale,
              "width_mm": width_mm, "dpi": dpi, "format": format, "roi": roi, "roi_id": roi_id,
              **_set(ExportOptions, options)}
    step, e = service.export(p, "export.map", _clean(params), backend=backend)
    return {"export_id": e.id, "backend": step.backend, "seconds": step.metrics.get("seconds"),
            "file": _path(p, step, "export"), "sidecar": _path(p, step, "sidecar"),
            "meta": _json_out(p, step, "sidecar"),
            "note": "exports are not recipe steps; listed by export_log, repeatable with export_again"}


# ---- SEM image stitching (phase 6.5) ---------------------------------------------------------
@server.tool()
def scan_sem_images(folder: str | None = None, files: list[str] | None = None,
                    strip_px: int | None = None) -> dict[str, Any]:
    """Read-only look at a folder (or files) of SEM images before stitching: counts per detector class (BSE / SE /
    other) and per detector name ($SM_DETECTOR, else the file name), data-bar height and its source, pixel size,
    magnification, stage X/Y per image, and the stitch sets (same magnification + pixel size + overlapping stage
    footprints; only one set is stitched at a time). Nothing is recorded."""
    from .modules import stitching  # noqa: PLC0415

    tiles = stitching.scan(folder, files, strip_px=strip_px)
    return {"counts": stitching.detector_counts(tiles), "detectors": stitching.detector_detail(tiles),
            "images": [{"name": t.name, "detector": t.detector, "class": t.detector_class,
                        "source": t.detector_source, "image_number": t.image_number,
                        "size_px": [t.file_width, t.file_height], "strip_px": t.strip_px,
                        "strip_source": t.strip_source, "pixel_size_um": t.pixel_size_um,
                        "magnification": t.magnification, "stage_xy_mm": [t.stage_x_mm, t.stage_y_mm],
                        "warnings": t.warnings} for t in tiles],
            "stitch_sets": [{"id": st.id, "label": st.label, "files": st.paths, "note": st.note,
                             "stage_check": stitching.stage_analysis([t for t in tiles if t.path in set(st.paths)])}
                            for st in stitching.stitch_sets(tiles)]}


@server.tool()
def stitch_images(project: str, folder: str | None = None, files: list[str] | None = None,
                  detector: Literal["BSE", "SE", "both"] = "BSE", se_layout: Literal["own", "bse"] = "own",
                  info_strip: Literal["none", "keep", "generate"] = "none",
                  blend: Literal["seam", "feather", "none"] = "seam", exclude: list[str] | None = None,
                  nudge: dict[str, tuple[float, float]] | None = None, use_stage: bool = True,
                  options: StitchOptions | None = None) -> dict[str, Any]:  # type: ignore[valid-type]
    """Stitch overlapping JEOL SEM images into one mosaic per detector (BSE = BED-*, SE = SED/...; never mixed;
    check counts with scan_sem_images first). se_layout 'bse' stitches BSE and places each SE tile with the BSE
    layout (small SE->BSE shift per image number) so both mosaics align. info_strip: none (default, for analysis),
    keep (original data bar of the bottom tile; single-column mosaics only), generate (new JEOL-style strip with
    ranges, image numbers and a redrawn scale bar). blend 'seam' (default) places tiles at subpixel positions and
    blends only in a narrow band around each seam (no ghosting); 'feather' blends whole overlaps; 'none' keeps raw
    pixel values. Different magnifications are never stitched together (scan_sem_images lists the stitch sets).
    use_stage=False ('Do not use stage positions', e.g. a faulty stage): placement only from image matching of all
    pairs (or manual_layout in options). exclude / nudge (dx, dy px)
    by file stem or image number. Returns per detector the TIFF, preview PNG, sidecar path, mosaic size, residuals,
    weak pairs and tiles placed by the stage only."""
    p = _project(project)
    params = {"folder": folder, "files": files, "detector": detector, "se_layout": se_layout,
              "info_strip": info_strip, "blend": blend, "exclude": exclude or [], "nudge": nudge or {},
              "use_stage": use_stage, **_set(StitchOptions, options)}
    step = service.run(p, "sem.stitch", _clean(params))
    out = _step_summary(step)
    for det in ("BSE", "SE"):
        if f"sidecar_{det}" in step.outputs:
            side = _json_out(p, step, f"sidecar_{det}")
            out[det] = {"tiff": _path(p, step, f"mosaic_{det}"), "preview": _path(p, step, f"preview_{det}"),
                        "sidecar": _path(p, step, f"sidecar_{det}"), "mosaic_px": side["mosaic_px"],
                        "pixel_size_um": side["pixel_size_um"], "residual_px": side["residual_px"],
                        "stage_only_tiles": side["stage_only_tiles"], "strip": side["strip"],
                        "stage_map": side["stage_map"].get("chosen"),
                        "notes": side.get("notes", []),            # stage not used, possible / confirmed repeats
                        "weak_pairs": [q for q in side["pairs"] if not q["ok"]],
                        "tiles": [{k: t[k] for k in ("name", "x_px", "y_px", "best_score", "placed_by")}
                                  for t in side["tiles"]]}
    return out


@server.tool()
def export_mosaic(project: str, stitch: str, detector: Literal["BSE", "SE"] = "BSE",
                  format: Literal["png", "tiff", "svg", "pdf", "bigtiff"] = "png",
                  mode: Literal["native", "width", "scale"] = "native", scale: float = 1.0,
                  width_mm: float = 120.0, dpi: int = 600, scale_bar: bool = True, info_strip: bool = True,
                  name: str = "") -> dict[str, Any]:
    """Export a stitched mosaic (step id of stitch_images) as PNG / TIFF / SVG / PDF or a tiled BigTIFF with pyramid
    (any size): native pixels, a fraction of the original (mode 'scale', scale 0.5 / 0.25 / 0.125) or a width in mm
    at a DPI, with an optional scale bar (from the mosaic pixel size; left out when the info strip, which has its own,
    is included) and the mosaic's info strip. Sizes that do not fit in memory are refused with the alternatives. The
    file stays in the project (path returned) with a JSON sidecar giving the pixel size of the exported image."""
    p = _project(project)
    step, e = service.export(p, "sem.export", {"stitch": stitch, "detector": detector, "format": format,
                                               "mode": mode, "scale": scale, "width_mm": width_mm, "dpi": dpi,
                                               "scale_bar": scale_bar, "info_strip": info_strip, "name": name})
    return {"export_id": e.id, "seconds": step.metrics.get("seconds"), "file": _path(p, step, "export"),
            "sidecar": _json_out(p, step, "sidecar")}


@server.tool()
def export_log(project: str) -> dict[str, Any]:
    """Exports of a project (not recipe steps): id, what (map / layer / mosaic), format, resolution, date, the file it
    was saved to and the copy kept in the project. Use export_again to repeat one with the same settings."""
    from . import exportlog  # noqa: PLC0415

    p = _project(project)
    return {"exports": [{"id": e.id, "what": e.label(), "op": e.op, "date": e.date, "file": e.file,
                         "project_file": str(p.root / e.project_file) if e.project_file else "",
                         "backend": e.backend, "params": e.params, "migrated_from_recipe": e.migrated}
                        for e in exportlog.load(p.root)]}


@server.tool()
def export_again(project: str, export_id: str, file: str | None = None,
                 backend: Backend | None = None) -> dict[str, Any]:
    """Repeat a logged export with the same settings (id from export_log); `file` = where to save it (default: the
    same file as before, if it had one). Uses the recorded backend unless `backend` is given."""
    p = _project(project)
    step, e = service.export_again(p, export_id, file, backend)
    return {"export_id": e.id, "file": e.file or _path(p, step, "export"), "sidecar": _path(p, step, "sidecar")}


@server.tool()
def undo(project: str) -> dict[str, Any]:
    """Undo the last recorded action of a project (recipe step, annotation change, backend switch)."""
    from . import history  # noqa: PLC0415

    p = _project(project)
    return {"undone": service.undo(p), **history.status(p)}


@server.tool()
def redo(project: str) -> dict[str, Any]:
    """Redo the last undone action."""
    from . import history  # noqa: PLC0415

    p = _project(project)
    return {"redone": service.redo(p), **history.status(p)}


@server.tool()
def recipe_history(project: str) -> dict[str, Any]:
    """Recipe steps (id, op, map, backend, stale, time) and the undo / redo stacks."""
    from . import history  # noqa: PLC0415

    p = _project(project)
    return {"steps": [{"id": s.id, "op": s.op, "map_id": s.params.get("map_id"), "backend": s.backend,
                       "stale": s.stale, "time": s.timestamp} for s in p.recipe().steps], **history.status(p)}


@server.tool()
def viewer_layers(project: str, map_id: str) -> dict[str, Any]:
    """Map types and overlays the viewer / export_map can show for a map (depends on the steps run so far)."""
    from . import layers  # noqa: PLC0415

    return layers.available(_project(project), map_id)


# ---- resources ------------------------------------------------------------------------------------
def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, default=str)


@server.resource("ti64://settings", name="settings", mime_type="application/json",
                 description="Default backend and shared figure style.")
def res_settings() -> str:
    return _dump(get_settings())


@server.resource("ti64://backends", name="backends", mime_type="application/json",
                 description="Backends and whether they can run here.")
def res_backends() -> str:
    return _dump(list_backends())


@server.resource("ti64://operations", name="operations", mime_type="application/json",
                 description="Service operations: description, backend use, params JSON schema, MCP tool.")
def res_operations() -> str:
    return _dump(list_operations())


@server.resource("ti64://projects", name="projects", mime_type="application/json",
                 description="Existing projects with backend and number of steps.")
def res_projects() -> str:
    out = []
    for n in list_projects():
        p = Project.open(projects_dir() / n)
        r = p.recipe()
        out.append({"name": n, "backend": p.info().backend, "maps": [s.map_id for s in p.samples()],
                    "steps": len(r.steps), "stale": sum(s.stale for s in r.steps)})
    return _dump(out)


@server.resource("ti64://projects/{project}", name="project_state", mime_type="application/json",
                 description="Project state: backend, maps, steps (id, op, map, backend, stale), undo / redo stacks.")
def res_project(project: str) -> str:
    return _dump({**_project(project).summary(), **recipe_history(project)})


@server.resource("ti64://projects/{project}/recipe", name="project_recipe", mime_type="application/json",
                 description="The project's recipe.json (every recorded step with params, inputs, outputs, versions).")
def res_recipe(project: str) -> str:
    return _project(project).recipe_path.read_text(encoding="utf-8")


@server.resource("ti64://projects/{project}/steps/{step_id}", name="project_step", mime_type="application/json",
                 description="One recipe step with absolute output paths and its small JSON outputs inline.")
def res_step(project: str, step_id: str) -> str:
    return _dump(get_step(project, step_id))


@server.resource("ti64://projects/{project}/samples/{map_id}", name="sample_sheet", mime_type="application/json",
                 description="Sample sheet of one map (build direction, input state, 4PB geometry, position).")
def res_sample(project: str, map_id: str) -> str:
    return _dump(get_sample_sheet(project, map_id))


def _private_wire() -> tuple[Any, Any]:
    """Protocol streams on private duplicates of fd 0 / 1; afterwards fd 1 and sys.stdout point to stderr, so stray
    output (pymtex prints the OR scan in cp1252, C libraries, child processes) can never reach the wire. mcp's own
    fd diversion is best effort and falls back to sharing sys.stdout, which corrupted the stream (2026-11-01)."""
    import io  # noqa: PLC0415
    import os  # noqa: PLC0415
    import sys  # noqa: PLC0415

    import anyio  # noqa: PLC0415

    sys.stdout.flush()
    wire_in = os.fdopen(os.dup(0), "rb")
    wire_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)                 # Windows CRT also rebinds STD_OUTPUT_HANDLE (inherited by child processes)
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)               # children get NUL as stdin: on Windows a child touching the wire pipe while our
    os.close(nul)                 # reader thread blocks in ReadFile hangs (GetFileType); MATLAB starts as a child
    sys.stdout = sys.stderr
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115
    return (anyio.wrap_file(io.TextIOWrapper(wire_in, encoding="utf-8", errors="replace")),
            anyio.wrap_file(io.TextIOWrapper(wire_out, encoding="utf-8")))


def main() -> None:
    import anyio  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    from .logs import setup_logging  # noqa: PLC0415

    setup_logging("mcp")                      # stdout is the MCP wire: log to %APPDATA%/ti64app/logs
    stdin, stdout = _private_wire()

    async def serve() -> None:
        low = server._lowlevel_server   # MCPServer.run_stdio_async, but with explicit streams
        async with stdio_server(stdin, stdout) as (read, write):
            await low.run(read, write, low.create_initialization_options())

    anyio.run(serve)


if __name__ == "__main__":
    main()
