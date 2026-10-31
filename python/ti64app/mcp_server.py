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
        out["stale_steps"] = _project(project).set_backend(backend)
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
def replay_project(project: str, backend: Literal["pymtex", "matlab"] | None = None) -> dict[str, Any]:
    """Re-run the project's recipe in a scratch copy and compare all outputs with the recorded ones."""
    rep = service.replay(_project(project), backend=backend)
    return {"ok": rep.ok, **rep.model_dump(mode="json")}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
