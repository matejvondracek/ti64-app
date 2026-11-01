"""MCP server: tools are thin wrappers over the service layer (the same functions the GUI calls).

Run:  python -m ti64app.mcp_server            (stdio transport)
Projects live in projects/<name>/; every tool that changes something is recorded in recipe.json.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import __version__, service
from .backends import backend_status, get_backend
from .project import Project, list_projects
from .sample import (BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file,
                     suggest_input_state)
from .settings import load_settings, save_preferences

server = MCPServer(
    name="ti64app",
    version=__version__,
    instructions=(
        "EBSD/BSE analysis of EBM Ti-6Al-4V. Work inside a project (create_project / list_projects). "
        "Before loading a map, enter its sample sheet with set_sample_sheet: the build direction is "
        "REQUIRED and must come from the user, never guessed. Map frame: x right, y down (EDAX setting 2); "
        "build-direction angle is measured from +x toward +y. 4PB geometry in mm, load in N."),
)


def _project(name: str) -> Project:
    return Project.open_or_create(name)


def _step_summary(step) -> dict[str, Any]:
    return {"step_id": step.id, "op": step.op, "backend": step.backend, "versions": step.versions,
            "metrics": step.metrics, "outputs": {k: v.path for k, v in step.outputs.items()}}


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
def set_backend(backend: Literal["pymtex", "matlab"], project: str | None = None) -> dict[str, Any]:
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


# ---- projects / sample sheets ---------------------------------------------------------------
@server.tool()
def projects() -> list[str]:
    """Names of existing projects."""
    return list_projects()


@server.tool()
def create_project(name: str, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Create projects/<name>/ (backend defaults to the Settings backend)."""
    p = Project.create(name, backend=backend or load_settings().backend)
    return p.summary()


@server.tool()
def project_summary(project: str) -> dict[str, Any]:
    """Backend, maps with sample sheets and recipe steps of a project."""
    return _project(project).summary()


@server.tool()
def suggest_sample_defaults(map_file: str) -> dict[str, str]:
    """Suggested map_id and input state for a map file (the user must confirm the input state)."""
    return {"map_id": map_id_from_file(map_file), "input_state": suggest_input_state(map_file)}


@server.tool()
def set_sample_sheet(
    project: str,
    map_file: str,
    specimen_id: str,
    input_state: Literal["raw", "oim_cleaned"],
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
) -> dict[str, Any]:
    """Enter the sample sheet of one map. Build direction is required (angle from map +x toward +y,
    or a 3-vector in map coordinates) and must be given by the user. 4PB: give all four geometry
    values (mm) or none; load in N. Position: map origin in the sample frame (mm) and the sample
    axes along map +x / +y."""
    bend = None
    geo = (outer_span_mm, inner_span_mm, width_mm, thickness_mm)
    if any(v is not None for v in geo):
        if any(v is None for v in geo):
            raise ValueError("4PB geometry: give outer_span_mm, inner_span_mm, width_mm and thickness_mm")
        bend = FourPointBending(outer_span_mm=outer_span_mm, inner_span_mm=inner_span_mm,
                                width_mm=width_mm, thickness_mm=thickness_mm, load_N=load_N)
    pos = None
    if any(v is not None for v in (origin_X_mm, origin_Y_mm, origin_Z_mm, map_x_axis, map_y_axis)):
        pos = MapPosition(**{k: v for k, v in dict(origin_X_mm=origin_X_mm, origin_Y_mm=origin_Y_mm,
                                                   origin_Z_mm=origin_Z_mm, map_x_axis=map_x_axis,
                                                   map_y_axis=map_y_axis).items() if v is not None})
    bd = BuildDirection(angle_deg=build_direction_angle_deg,
                        vector=tuple(build_direction_vector) if build_direction_vector else None)
    sheet = SampleSheet(map_id=map_id or map_id_from_file(map_file), specimen_id=specimen_id,
                        map_file=map_file, input_state=input_state, build_direction=bd,
                        bending=bend, position=pos, notes=notes)
    step = service.run(_project(project), "sample.set", sheet)
    return {**_step_summary(step), "map_id": sheet.map_id, "build_direction_unit": bd.unit_vector}


@server.tool()
def get_sample_sheet(project: str, map_id: str) -> dict[str, Any]:
    """The stored sample sheet of a map."""
    return _project(project).sample(map_id).model_dump(mode="json")


# ---- loaders --------------------------------------------------------------------------------
@server.tool()
def load_map(project: str, map_id: str, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Load a map's .ang (its sample sheet must exist) with the project backend or `backend`."""
    step = service.run(_project(project), "ebsd.load", {"map_id": map_id}, backend=backend)
    return _step_summary(step)


@server.tool()
def read_up2_header(project: str, file: str) -> dict[str, Any]:
    """Header of an EDAX .up2 pattern file (patterns are not loaded)."""
    p = _project(project)
    step = service.run(p, "up2.header", {"file": file})
    import json  # noqa: PLC0415

    return {**_step_summary(step), "header": json.loads((p.root / step.outputs["header"].path).read_text())}


@server.tool()
def load_bse(project: str, image: str, txt: str | None = None) -> dict[str, Any]:
    """Load a JEOL BSE image and its .txt metadata (stage X/Y/Z/tilt/rotation, WD, kV, mag, pixel size)."""
    p = _project(project)
    step = service.run(p, "bse.load", {"image": image, "txt": txt})
    import json  # noqa: PLC0415

    return {**_step_summary(step), "metadata": json.loads((p.root / step.outputs["meta"].path).read_text())}


@server.tool()
def cleanup_map(project: str, map_id: str, overrides: dict[str, Any] | None = None,
                backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Cleanup with the preset of the map's input state (raw: CI < 0.1 removed; OIM-cleaned: no CI
    filter; both: EBSD_part2 noise removal at 3°, indexed grains <= 2 px and voids <= 4 px refilled).
    overrides: ci_threshold, spike_removal, spike_angle_deg, noise_angle_deg, noise_max_indexed_px,
    noise_max_void_px, fill, hq_filter, hq_alpha, hq_threshold_deg. The loaded map is not changed."""
    p = _project(project)
    step = service.run(p, "ebsd.cleanup", {"map_id": map_id, "overrides": overrides or {}}, backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "report": json.loads((p.root / step.outputs["report"].path).read_text())}


@server.tool()
def calc_grains(project: str, map_id: str, hagb_deg: float = 15.0, lagb_deg: float = 2.0,
                burgers_tol_deg: float = 3.0, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Alpha grains bounded by HAGB >= hagb_deg (LAGB lagb_deg..hagb_deg inside grains) on the newest
    cleaned map; boundary lengths per class (LAGB, HAGB, alpha/beta interface, Burgers types 10.53 /
    60 / 60.83 / 63.26 / 90° within burgers_tol_deg)."""
    p = _project(project)
    step = service.run(p, "grains.calc", {"map_id": map_id, "grains": {"hagb_deg": hagb_deg, "lagb_deg": lagb_deg},
                                          "boundaries": {"burgers_tol_deg": burgers_tol_deg}}, backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "summary": json.loads((p.root / step.outputs["summary"].path).read_text())}


@server.tool()
def compute_map(project: str, map_id: str, kind: Literal["kam", "grod", "gos", "gnd"], kam_order: int = 1,
                kam_threshold_deg: float = 5.0, gnd_stencil: Literal["1hop", "oneSided", "full"] = "1hop",
                backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Per-pixel KAM (kernel order 1-3, cutoff in deg), GROD, GOS (deg) or GND (1/m^2, alpha:
    basal/prismatic/pyramidal <a> + pyramidal <c+a>) inside the newest grains of the map."""
    p = _project(project)
    step = service.run(p, "map.compute", {"map_id": map_id, "kind": kind, "kam_order": kam_order,
                                          "kam_threshold_deg": kam_threshold_deg, "gnd_stencil": gnd_stencil},
                       backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "stats": json.loads((p.root / step.outputs["stats"].path).read_text())}


@server.tool()
def make_figure(project: str, map_id: str,
                kind: Literal["ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd",
                              "boundaries", "burgers", "ipf_key"],
                overlays: list[Literal["hagb", "lagb", "interface", "burgers"]] | None = None,
                phases: list[str] | None = None,
                direction: Literal["X", "Y", "Z", "build", "custom"] | None = None,
                custom_vector: list[float] | None = None, key: str | None = None,
                clim: list[float] | None = None) -> dict[str, Any]:
    """Draw a map with the project backend; returns the PNG path. IPF key and direction default to
    Settings per phase ('build' = build direction from the sample sheet). Fit/PRIAS report clearly
    when the file does not contain them."""
    p = _project(project)
    params = {"map_id": map_id, "kind": kind, "overlays": overlays or [], "phases": phases,
              "direction": direction, "custom_vector": custom_vector, "key": key, "clim": clim}
    step = service.run(p, "figure", {k: v for k, v in params.items() if v is not None})
    import json  # noqa: PLC0415

    return {**_step_summary(step), "png": str(p.root / step.outputs["figure"].path),
            "meta": json.loads((p.root / step.outputs["meta"].path).read_text())}


@server.tool()
def compare_with_oim(project: str, map_id: str, image: str, kind: Literal["ipf", "ci"] = "ipf",
                     key: str = "ipfTSLKey", direction: Literal["X", "Y", "Z", "build"] = "Z") -> dict[str, Any]:
    """Compare our IPF/CI map (from the LOADED, not cleaned, data) with an OIM image; all 8
    flips/rotations are scored so a reference-frame mismatch is reported."""
    p = _project(project)
    step = service.run(p, "compare.oim", {"map_id": map_id, "image": image, "kind": kind, "key": key,
                                          "direction": direction})
    import json  # noqa: PLC0415

    return {**_step_summary(step), "result": json.loads((p.root / step.outputs["result"].path).read_text())}


@server.tool()
def reconstruct_beta(project: str, map_id: str, params: dict[str, Any] | None = None,
                     backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Prior-beta reconstruction of the LOADED map (EBSD_part2.m method; Niessen 2022, Hielscher 2022).
    params (ReconParams, degrees): method 'variantGraph'|'graph', vgThreshold 5, vgTolerance 1.5, vgNumIter 3,
    vgInflation 1.05, segAngle 1.5, orMode 'limit'|'burgers'|'refined' with orMaxDev 1 (refined OR used only
    if <= orMaxDev from Burgers), mergeMode 'single'|'progressive', mergeThreshold 3, inclusionMaxSize 5,
    histMinECD 5, sweep [[mode, deg], ...]. Returns the summary: OR deviation and OR used, grains,
    reconstructed fraction, fit, ECD, bridged steps / split noise, merge sweep, Ti variant/packet statistics,
    grain size and caliper along/across the build direction."""
    p = _project(project)
    step = service.run(p, "recon.run", {"map_id": map_id, "params": params or {}}, backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "summary": json.loads((p.root / step.outputs["summary"].path).read_text())}


def _recon_backend(p, map_id: str, recon: str | None) -> str | None:
    s = p.recipe().step(recon) if recon else service.latest_step(p, ("recon.run",), map_id=map_id)
    return s.backend if s else None


@server.tool()
def recon_figure(project: str, map_id: str,
                 kind: Literal["parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                               "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist"],
                 recon: str | None = None, direction: Literal["X", "Y", "Z", "build", "custom"] | None = None,
                 key: str | None = None) -> dict[str, Any]:
    """Figure of the newest (or given) reconstruction, drawn by the backend that computed it: parent grain IPF
    map, alpha IPF with prior-beta boundaries, fit / variant / packet maps, boundary misorientation map,
    beta IPF key, histograms (neighbour misorientation, fit, ECD, variants per grain). Returns the PNG path."""
    p = _project(project)
    params = {"map_id": map_id, "kind": kind, "recon": recon, "direction": direction, "key": key}
    step = service.run(p, "recon.figure", {k: v for k, v in params.items() if v is not None},
                       backend=_recon_backend(p, map_id, recon))
    import json  # noqa: PLC0415

    return {**_step_summary(step), "png": str(p.root / step.outputs["figure"].path),
            "meta": json.loads((p.root / step.outputs["meta"].path).read_text())}


@server.tool()
def recon_sweep(project: str, map_id: str, grid: dict[str, list[Any]], base: dict[str, Any] | None = None,
                thumbnails: bool = True, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Reconstruction-parameter sweep: every combination of `grid` (ReconParams names -> value lists, e.g.
    {"vgThreshold": [5, 8], "mergeThreshold": [0.5, 3, 6]}) on top of `base`. Per run: reconstructed fraction,
    parent-fit quantiles, beta grain count, area-weighted ECD, bridged steps, split noise, thumbnail PNG."""
    p = _project(project)
    params = {"map_id": map_id, "grid": grid, "thumbnails": thumbnails, "base": {"sweep": [], **(base or {})}}
    step = service.run(p, "recon.sweep", params, backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "table": json.loads((p.root / step.outputs["table"].path).read_text()),
            "thumbnails": [str(p.root / r.path) for k, r in sorted(step.outputs.items()) if k.startswith("thumb_")]}


@server.tool()
def grain_size(project: str, map_id: str, params: dict[str, Any] | None = None,
               backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Grain size per ISO 13067 / ASTM E2627 rules on the newest grains.calc partition (lath threshold = its
    hagb_deg, default 15°) and, if present, the newest reconstruction. params (GrainSizeParams): min_px 5,
    exclude_edge true, lath_min_aspect 2, lath_angle_bin_deg 5, colony {burgers_tol_deg 3}, beta_line_spacing_um 1,
    beta_min_ecd_um (default recon histMinECD), use_recon true. Returns alpha ECD (number/area-weighted), lath
    thickness (intercept + minor axis), colony ECD, prior-beta ECD, width/length vs build direction (E112 mean
    intercepts + calipers), retained-beta fraction, log-normal fits, warnings, and the CSV/XLSX table paths."""
    p = _project(project)
    step = service.run(p, "grainsize.run", {"map_id": map_id, **(params or {})}, backend=backend)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "summary": json.loads((p.root / step.outputs["summary"].path).read_text(encoding="utf-8")),
            "tables": {k: str(p.root / r.path) for k, r in step.outputs.items() if r.kind in ("csv", "xlsx")}}


@server.tool()
def grain_size_figure(project: str, map_id: str,
                      kind: Literal["alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd",
                                    "colony_ecd_area", "beta_ecd", "beta_width", "beta_length", "colony_map"],
                      grainsize: str | None = None) -> dict[str, Any]:
    """Histogram with log-normal fit (or the colony map) of the newest (or given) grain-size step, drawn by the
    backend that computed it. Returns the PNG path."""
    p = _project(project)
    s = p.recipe().step(grainsize) if grainsize else service.latest_step(p, ("grainsize.run",), map_id=map_id)
    params = {"map_id": map_id, "kind": kind, "grainsize": grainsize}
    step = service.run(p, "grainsize.figure", {k: v for k, v in params.items() if v is not None},
                       backend=s.backend if s else None)
    return {**_step_summary(step), "png": str(p.root / step.outputs["figure"].path)}


@server.tool()
def compare_oim_grain_size(project: str, map_id: str, file: str, phase: int | None = None) -> dict[str, Any]:
    """Validate the alpha grain ECD of the newest grain-size step against an OIM Analysis grain file export
    (same min-pixel / edge rules applied to the OIM grains; means, log-normal fits, two-sample KS, histogram)."""
    p = _project(project)
    s = service.latest_step(p, ("grainsize.run",), map_id=map_id)
    params = {"map_id": map_id, "file": file, "phase": phase}
    step = service.run(p, "grainsize.compare_oim", {k: v for k, v in params.items() if v is not None},
                       backend=s.backend if s else None)
    import json  # noqa: PLC0415

    return {**_step_summary(step), "png": str(p.root / step.outputs["comparison"].path),
            "result": json.loads((p.root / step.outputs["result"].path).read_text(encoding="utf-8"))}


@server.tool()
def replay_project(project: str, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Re-run the project's recipe in a scratch copy and compare all outputs with the recorded ones."""
    rep = service.replay(_project(project), backend=backend)
    return {"ok": rep.ok, **rep.model_dump(mode="json")}


# ---- phase 5: annotations, crop, export, undo / redo ---------------------------------------------
@server.tool()
def get_annotations(project: str, map_id: str) -> list[dict[str, Any]]:
    """Annotations and ROIs of a map (coordinates in um, map frame x right / y down)."""
    from .annotations import load_annotations  # noqa: PLC0415

    return [a.model_dump(mode="json") for a in load_annotations(_project(project), map_id)]


@server.tool()
def set_annotations(project: str, map_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the annotations / ROIs of a map (recorded, undoable). Each item: type (text, arrow, line,
    polyline, point, crack, roi_rect, roi_poly), xy [[x, y], ...] in um, optional text (label / ROI name),
    color '#rrggbb', width_pt, size_pt, export (bool). Crack paths are shown by the 'crack' overlay."""
    items = [{**it, "map_id": map_id} for it in items]
    step = service.run(_project(project), "annotations.set", {"map_id": map_id, "items": items})
    return _step_summary(step)


@server.tool()
def crop_map(project: str, map_id: str, new_map_id: str, roi: list[float] | None = None,
             roi_id: str | None = None) -> dict[str, Any]:
    """Crop a map to an ROI (x0, y0, x1, y1 in um, or the id / name of an ROI annotation): writes the .ang rows
    inside it to maps/<new_map_id>.ang in the project and a sample sheet copied from the parent. Then load_map it."""
    params = {"map_id": map_id, "new_map_id": new_map_id, "roi": roi, "roi_id": roi_id}
    step = service.run(_project(project), "ebsd.crop", {k: v for k, v in params.items() if v is not None})
    return _step_summary(step)


@server.tool()
def export_map(project: str, map_id: str, kind: str = "ipf", overlays: list[str] | None = None,
               mode: Literal["native", "scale", "width"] = "native", scale: Literal[2, 4, 8] = 2,
               width_mm: float = 120.0, dpi: int = 600, format: Literal["png", "tiff", "svg", "pdf"] = "png",
               roi: list[float] | None = None, roi_id: str | None = None, layer: dict[str, Any] | None = None,
               options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Export a map layer (kind: ipf, phase, ci, iq, kam, grod, gos, gnd, parent_ipf, recon_fit, variant, packet)
    with overlays (hagb, lagb, interface, burgers, beta_gb, crack) and the map's annotations.
    mode native = one pixel per measurement, scale = 2/4/8x nearest neighbour (PNG / TIFF, pixel size in the file
    and the sidecar); width = figure width_mm wide at dpi drawn by the backend (PNG / TIFF / SVG / PDF).
    layer: further LayerParams (direction, key, ipf_keys, clim, source steps); options: annotations, roi_outlines,
    scale_bar, title, colorbar, legend, line_px, background, name. Returns the file, the JSON sidecar and its content."""
    import json  # noqa: PLC0415

    p = _project(project)
    params = {"map_id": map_id, "layer": {"kind": kind, **(layer or {})}, "overlays": overlays or [], "mode": mode,
              "scale": scale, "width_mm": width_mm, "dpi": dpi, "format": format, **(options or {})}
    if roi is not None:
        params["roi"] = roi
    if roi_id is not None:
        params["roi_id"] = roi_id
    step = service.run(p, "export.map", params)
    side = p.root / step.outputs["sidecar"].path
    return {**_step_summary(step), "file": str(p.root / step.outputs["export"].path), "sidecar": str(side),
            "meta": json.loads(side.read_text(encoding="utf-8"))}


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


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
