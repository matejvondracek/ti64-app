"""NiceGUI front end (localhost). Pages: Project (/), Sample sheet (/sample), Settings (/settings).

All actions go through ti64app.service (recorded in recipe.json), the same as the MCP tools.
Run:  python -m ti64app.gui.app      -> http://127.0.0.1:8064
"""
from __future__ import annotations

import traceback
from pathlib import Path

from nicegui import run as ng_run
from nicegui import ui

from .. import service
from ..backends import backend_status, get_backend
from ..project import Project, list_projects, projects_dir
from ..sample import (BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file,
                      suggest_input_state)
from ..settings import IpfKeySetting, load_settings, save_preferences

AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
STATE: dict = {"project": None}


def current_project() -> Project | None:
    name = STATE["project"]
    if not name:
        return None
    root = projects_dir() / name
    return Project.open(root) if (root / "project.json").is_file() else None


def _nav() -> None:
    with ui.header().classes("items-center gap-4"):
        ui.label("Ti64 EBSD/BSE").classes("text-lg font-bold")
        ui.link("Project", "/").classes("text-white")
        ui.link("Sample sheet", "/sample").classes("text-white")
        ui.link("Settings", "/settings").classes("text-white")
        p = current_project()
        ui.label(f"project: {p.name} ({p.info().backend})" if p else "no project open").classes("ml-auto")


def _error(e: Exception) -> None:
    ui.notify(f"{type(e).__name__}: {e}", type="negative", multi_line=True, timeout=10000)
    traceback.print_exc()


# ---- Project page -----------------------------------------------------------------------------
def project_page() -> None:
    _nav()
    with ui.column().classes("p-4 gap-4 w-full max-w-5xl"):
        with ui.row().classes("items-end gap-2"):
            names = list_projects(projects_dir())
            sel = ui.select(names, label="Open project", value=STATE["project"]).classes("w-64")
            ui.button("Open", on_click=lambda: (STATE.update(project=sel.value), ui.navigate.to("/")))
            new = ui.input("New project name").classes("w-64").mark("new_project")

            def create():
                try:
                    Project.create(new.value.strip(), base=projects_dir(),
                                   backend=load_settings().backend)
                    STATE["project"] = new.value.strip()
                    ui.navigate.to("/")
                except Exception as e:  # noqa: BLE001
                    _error(e)
            ui.button("Create", on_click=create).mark("create_project")

        p = current_project()
        if p is None:
            ui.label("Open or create a project.")
            return
        info = p.info()
        ui.label(f"{info.name} — backend {info.backend} — {p.root}").classes("text-gray-600")

        ui.label("Maps (sample sheets)").classes("text-md font-bold")
        sheets = p.samples()
        if not sheets:
            ui.label("No sample sheet yet: enter one on the Sample sheet page.")
        for s in sheets:
            with ui.row().classes("items-center gap-4"):
                ui.label(f"{s.map_id}: {s.specimen_id}, {s.input_state}, BD {tuple(round(c, 3) for c in s.build_direction.unit_vector)}")

                async def load(map_id=s.map_id):
                    n = ui.notification(f"loading {map_id} with {p.info().backend} ...", spinner=True, timeout=None)
                    try:
                        step = await ng_run.io_bound(service.run, p, "ebsd.load", {"map_id": map_id})
                        ui.notify(f"{map_id}: {step.metrics.get('n_points')} points, "
                                  f"load {step.metrics.get('load_s')} s", type="positive")
                        ui.navigate.to("/")
                    except Exception as e:  # noqa: BLE001
                        _error(e)
                    finally:
                        n.dismiss()
                ui.button("Load map", on_click=load).props("dense")

        ui.label("Other files").classes("text-md font-bold")
        with ui.row().classes("items-end gap-2"):
            up2 = ui.input(".up2 file").classes("w-96")
            ui.button("Read .up2 header", on_click=lambda: _run_simple(p, "up2.header", {"file": up2.value}))
        with ui.row().classes("items-end gap-2"):
            img = ui.input("BSE image (.tif/.bmp)").classes("w-96")
            txt = ui.input("JEOL .txt (optional)").classes("w-64")
            ui.button("Load BSE", on_click=lambda: _run_simple(p, "bse.load", {"image": img.value, "txt": txt.value or None}))

        ui.label("Recipe").classes("text-md font-bold")
        rows = [{"id": s.id, "op": s.op, "backend": s.backend or "-", "stale": "stale" if s.stale else "",
                 "time": s.timestamp, "metrics": ", ".join(f"{k}={v}" for k, v in s.metrics.items())}
                for s in p.recipe().steps]
        ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in
                          ("id", "op", "backend", "stale", "time", "metrics")], rows=rows).classes("w-full")

        async def do_replay():
            n = ui.notification("replaying ...", spinner=True, timeout=None)
            try:
                rep = await ng_run.io_bound(service.replay, p)
                bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]
                ui.notify("replay reproduced all outputs" if rep.ok else f"replay: {len(bad)} differences, "
                          f"{len(rep.input_changes)} input changes", type="positive" if rep.ok else "warning")
            except Exception as e:  # noqa: BLE001
                _error(e)
            finally:
                n.dismiss()
        ui.button("Replay recipe", on_click=do_replay)


def _run_simple(p: Project, op: str, params: dict) -> None:
    try:
        step = service.run(p, op, params)
        ui.notify(f"{op} done ({step.id})", type="positive")
        ui.navigate.to("/")
    except Exception as e:  # noqa: BLE001
        _error(e)


# ---- Sample sheet page ------------------------------------------------------------------------
def sample_page() -> None:
    _nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-3xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        existing = {s.map_id: s for s in p.samples()}
        pick = ui.select(["(new)"] + list(existing), value="(new)", label="Edit sample sheet").classes("w-64")

        f = {}
        f["map_file"] = ui.input("Map file (.ang)").classes("w-full").mark("map_file")
        f["map_id"] = ui.input("Map ID").classes("w-64").mark("map_id")
        f["specimen_id"] = ui.input("Specimen ID (required)").classes("w-64").mark("specimen_id")
        f["input_state"] = ui.toggle({"raw": "raw", "oim_cleaned": "OIM-cleaned"}, value=None).mark("input_state")
        ui.label("Input state is suggested from the file name; confirm it.").classes("text-xs text-gray-500")

        def suggest():
            if f["map_file"].value:
                if not f["map_id"].value:
                    f["map_id"].value = map_id_from_file(f["map_file"].value)
                if f["input_state"].value is None:
                    f["input_state"].value = suggest_input_state(f["map_file"].value)
        f["map_file"].on("blur", suggest)

        ui.label("Build direction (required, map frame: x right, y down)").classes("font-bold")
        f["bd_mode"] = ui.toggle({"angle": "angle", "vector": "vector"}, value="angle")
        f["bd_angle"] = ui.number("angle from +x toward +y [deg]", value=None).mark("bd_angle")
        with ui.row():
            f["bd_x"], f["bd_y"], f["bd_z"] = (ui.number(c, value=None).classes("w-24") for c in ("x", "y", "z"))

        ui.label("4-point bending (mm, N) — all four geometry values or none").classes("font-bold")
        with ui.row():
            for k, lab in (("outer_span_mm", "outer span"), ("inner_span_mm", "inner span"),
                           ("width_mm", "width"), ("thickness_mm", "thickness"), ("load_N", "load [N]")):
                f[k] = ui.number(lab, value=None).classes("w-28")

        ui.label("Map position on the specimen (sample frame, mm)").classes("font-bold")
        with ui.row():
            for k in ("origin_X_mm", "origin_Y_mm", "origin_Z_mm"):
                f[k] = ui.number(k, value=None).classes("w-28")
            f["map_x_axis"] = ui.select(AXES, value=None, label="map +x along").classes("w-28")
            f["map_y_axis"] = ui.select(AXES, value=None, label="map +y along").classes("w-28")
        f["notes"] = ui.textarea("Notes").classes("w-full")

        def fill(map_id):
            s = existing.get(map_id)
            if s is None:
                return
            f["map_file"].value, f["map_id"].value, f["specimen_id"].value = s.map_file, s.map_id, s.specimen_id
            f["input_state"].value = s.input_state
            bd = s.build_direction
            f["bd_mode"].value = "angle" if bd.angle_deg is not None else "vector"
            f["bd_angle"].value = bd.angle_deg
            if bd.vector:
                f["bd_x"].value, f["bd_y"].value, f["bd_z"].value = bd.vector
            for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm", "load_N"):
                f[k].value = getattr(s.bending, k) if s.bending else None
            for k in ("origin_X_mm", "origin_Y_mm", "origin_Z_mm", "map_x_axis", "map_y_axis"):
                f[k].value = getattr(s.position, k) if s.position else None
            f["notes"].value = s.notes
        pick.on_value_change(lambda e: fill(e.value))

        def save():
            try:
                sheet = sheet_from_form({k: w.value for k, w in f.items()})
                step = service.run(p, "sample.set", sheet)
                ui.notify(f"sample sheet {sheet.map_id} saved ({step.id})", type="positive")
            except Exception as e:  # noqa: BLE001
                _error(e)
        ui.button("Save sample sheet", on_click=save).mark("save_sample")


def sheet_from_form(v: dict) -> SampleSheet:
    """Build a SampleSheet from form values (shared by the GUI and its tests)."""
    if v.get("bd_mode") == "vector":
        bd = BuildDirection(vector=(v.get("bd_x") or 0.0, v.get("bd_y") or 0.0, v.get("bd_z") or 0.0))
    else:
        if v.get("bd_angle") is None:
            raise ValueError("build direction is required")
        bd = BuildDirection(angle_deg=float(v["bd_angle"]))
    geo = [v.get(k) for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm")]
    bend = None
    if any(g is not None for g in geo):
        if any(g is None for g in geo):
            raise ValueError("4PB: enter outer span, inner span, width and thickness (or none)")
        bend = FourPointBending(outer_span_mm=geo[0], inner_span_mm=geo[1], width_mm=geo[2],
                                thickness_mm=geo[3], load_N=v.get("load_N"))
    pos_keys = ("origin_X_mm", "origin_Y_mm", "origin_Z_mm", "map_x_axis", "map_y_axis")
    pos = None
    if any(v.get(k) is not None for k in pos_keys):
        pos = MapPosition(**{k: v[k] for k in pos_keys if v.get(k) is not None})
    if not v.get("input_state"):
        raise ValueError("choose the input state (raw / OIM-cleaned)")
    return SampleSheet(map_id=v.get("map_id") or map_id_from_file(v["map_file"]),
                       specimen_id=(v.get("specimen_id") or "").strip(), map_file=v.get("map_file") or "",
                       input_state=v["input_state"], build_direction=bd, bending=bend, position=pos,
                       notes=v.get("notes") or "")


# ---- Settings page ----------------------------------------------------------------------------
def settings_page() -> None:
    _nav()
    s = load_settings()
    status = backend_status()
    with ui.column().classes("p-4 gap-3 w-full max-w-3xl"):
        ui.label("Analysis backend").classes("text-md font-bold")
        avail = {n: n for n, r in status.items() if not r}
        radio = ui.radio(avail, value=s.backend if s.backend in avail else next(iter(avail), None)).props("inline").mark("backend")
        for n, r in status.items():
            if r:
                ui.label(f"{n}: unavailable — {r}").classes("text-orange-700")
            else:
                running = n == "matlab" and getattr(get_backend(n), "running", False)
                ui.label(f"{n}: available" + (" (engine running)" if running else "")).classes("text-green-700")
        p = current_project()
        if p:
            ui.label(f"Project {p.name} uses {p.info().backend}. Changing it marks backend results stale.")

        ui.label("IPF colour key (shared by both renderers)").classes("text-md font-bold")
        keys = {}
        for phase, k in s.figure.ipf.items():
            with ui.row().classes("items-center"):
                ui.label(phase).classes("w-24")
                keys[phase] = (ui.select(["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"], value=k.key).classes("w-36"),
                               ui.select(["X", "Y", "Z", "build", "custom"], value=k.direction).classes("w-28"))
        with ui.row():
            dpi = ui.number("DPI", value=s.figure.dpi).classes("w-24")
            width = ui.number("width [cm]", value=s.figure.width_cm).classes("w-24")
            lw = ui.number("boundary line width", value=s.figure.boundary_line_width).classes("w-36")

        def save():
            try:
                st = load_settings()
                st.backend = radio.value
                for phase, (k, d) in keys.items():
                    st.figure.ipf[phase] = IpfKeySetting(key=k.value, direction=d.value)
                st.figure.dpi, st.figure.width_cm = int(dpi.value), float(width.value)
                st.figure.boundary_line_width = float(lw.value)
                path = save_preferences(st)
                msg = f"saved to {path}"
                pr = current_project()
                if pr:
                    n = pr.set_backend(radio.value)
                    msg += f"; project backend {radio.value}" + (f", {n} steps marked stale" if n else "")
                ui.notify(msg, type="positive")
            except Exception as e:  # noqa: BLE001
                _error(e)
        ui.button("Save settings", on_click=save).mark("save_settings")


def build() -> None:
    ui.page("/")(project_page)
    ui.page("/sample")(sample_page)
    ui.page("/settings")(settings_page)


def main(port: int = 8064, project: str | None = None) -> None:
    STATE["project"] = project
    build()
    ui.run(host="127.0.0.1", port=port, title="Ti64 EBSD/BSE", reload=False, show=False,
           storage_secret=None, favicon="🔬")

