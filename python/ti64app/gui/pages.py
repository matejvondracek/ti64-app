"""GUI pages added in phase 5: project browser (/), recipe history with undo / redo / replay (/recipe),
parameter panels for every operation (/ops) and the full Settings page (/settings).
The analysis pages (sample sheet, maps, reconstruction, grain size) stay in app.py, the viewer in viewer.py."""
from __future__ import annotations

import json
import types
import typing

from nicegui import run as ng_run
from nicegui import ui
from pydantic import BaseModel

from .. import service
from ..backends import backend_status, get_backend
from ..project import Project, list_projects, projects_dir
from ..settings import (FigureStyle, IpfKeySetting, Paths, find_settings_file, load_settings, preferences_path,
                        save_preferences)

PIPELINE = ("ebsd.load", "ebsd.cleanup", "grains.calc", "map.compute", "recon.run", "grainsize.run", "export.map")


# ---- project browser ------------------------------------------------------------------------------
def _project_row(name: str) -> dict:
    p = Project(projects_dir() / name)
    try:
        info, r = p.info(), p.recipe()
        last = max((s.timestamp for s in r.steps), default=info.created)
        return {"name": name, "backend": info.backend, "maps": ", ".join(s.map_id for s in p.samples()),
                "steps": len(r.steps), "stale": sum(s.stale for s in r.steps), "created": info.created[:16],
                "last": last[:16]}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "backend": "?", "maps": f"unreadable: {e}", "steps": 0, "stale": 0, "created": "", "last": ""}


def project_page(STATE, current_project, nav, error) -> None:
    nav()
    with ui.column().classes("p-4 gap-3 w-full max-w-6xl"):
        ui.label(f"Projects in {projects_dir()}").classes("text-md font-bold")
        rows = [_project_row(n) for n in list_projects(projects_dir())]
        cols = [{"name": k, "label": k, "field": k, "align": "left", "sortable": True}
                for k in ("name", "backend", "maps", "steps", "stale", "created", "last")]
        tbl = ui.table(columns=cols, rows=rows, row_key="name", selection="single",
                       pagination=15).classes("w-full").props("dense").mark("project_table")
        with ui.row().classes("items-end gap-2"):
            def open_sel():
                if tbl.selected:
                    STATE["project"] = tbl.selected[0]["name"]
                    ui.navigate.to("/")
            ui.button("Open selected", icon="folder_open", on_click=open_sel).mark("open_project")
            names = [r["name"] for r in rows]
            sel = ui.select(names, label="Open project", value=STATE["project"]).classes("w-64")
            ui.button("Open", on_click=lambda: (STATE.update(project=sel.value), ui.navigate.to("/")))
            new = ui.input("New project name").classes("w-64").mark("new_project")

            def create():
                try:
                    Project.create(new.value.strip(), base=projects_dir(), backend=load_settings().backend)
                    STATE["project"] = new.value.strip()
                    ui.navigate.to("/")
                except Exception as e:  # noqa: BLE001
                    error(e)
            ui.button("Create", on_click=create).mark("create_project")

        p = current_project()
        if p is None:
            ui.label("Open or create a project.")
            return
        info = p.info()
        ui.separator()
        ui.label(f"{info.name} — backend {info.backend} — {p.root}").classes("text-gray-600").mark("project_info")
        ui.label("Maps").classes("text-md font-bold")
        sheets = p.samples()
        if not sheets:
            ui.label("No sample sheet yet: enter one on the Sample sheet page.")
        steps = [s for s in p.recipe().steps if not s.stale]
        for sh in sheets:
            done = {s.op for s in steps if s.params.get("map_id") == sh.map_id}
            with ui.card().classes("w-full p-2"):
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.label(sh.map_id).classes("font-bold w-48")
                    ui.label(f"{sh.specimen_id}, {sh.input_state}, BD {tuple(round(c, 3) for c in sh.build_direction.unit_vector)}").classes("text-sm")
                    for op in PIPELINE:
                        ui.badge(op.split(".")[0] if op != "map.compute" else "maps",
                                 color="green" if op in done else "grey-5").props("outline" if op not in done else "")
                    ui.space()

                    async def load(map_id=sh.map_id):
                        n = ui.notification(f"loading {map_id} with {p.info().backend} ...", spinner=True, timeout=None)
                        try:
                            step = await ng_run.io_bound(service.run, p, "ebsd.load", {"map_id": map_id})
                            ui.notify(f"{map_id}: {step.metrics.get('n_points')} points, "
                                      f"load {step.metrics.get('load_s')} s", type="positive")
                            ui.navigate.to("/")
                        except Exception as e:  # noqa: BLE001
                            error(e)
                        finally:
                            n.dismiss()
                    ui.button("Load map", on_click=load).props("dense").mark(f"load_{sh.map_id}")
                    ui.button("View", icon="visibility", on_click=lambda: ui.navigate.to("/viewer")).props("dense flat")
                ui.label(sh.map_file).classes("text-xs text-gray-500 break-all")

        ui.label("Other files").classes("text-md font-bold")
        with ui.row().classes("items-end gap-2"):
            up2 = ui.input(".up2 file").classes("w-96")
            ui.button("Read .up2 header", on_click=lambda: _run_simple(p, "up2.header", {"file": up2.value}, error))
        with ui.row().classes("items-end gap-2"):
            img = ui.input("BSE image (.tif/.bmp)").classes("w-96")
            txt = ui.input("JEOL .txt (optional)").classes("w-64")
            ui.button("Load BSE", on_click=lambda: _run_simple(p, "bse.load", {"image": img.value, "txt": txt.value or None}, error))
        ui.link("Recipe history, undo / redo and replay →", "/recipe")


def _run_simple(p: Project, op: str, params: dict, error) -> None:
    try:
        step = service.run(p, op, params)
        ui.notify(f"{op} done ({step.id})", type="positive")
        ui.navigate.to("/")
    except Exception as e:  # noqa: BLE001
        error(e)


# ---- recipe page --------------------------------------------------------------------------------------
def recipe_page(current_project, nav, error) -> None:
    nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-7xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        from .. import history  # noqa: PLC0415

        hs = history.status(p)
        with ui.row().classes("items-center gap-2"):
            ui.label("Recipe").classes("text-md font-bold")
            ui.label(f"{len(p.recipe().steps)} steps — undo: {hs['undo'][-1] if hs['undo'] else '–'}; "
                     f"redo: {hs['redo'][-1] if hs['redo'] else '–'}").classes("text-sm text-gray-600").mark("undo_status")
        rows = [{"id": s.id, "op": s.op, "map": s.params.get("map_id", ""), "backend": s.backend or "-",
                 "stale": "stale" if s.stale else "", "time": s.timestamp[:19].replace("T", " "),
                 "metrics": ", ".join(f"{k}={v}" for k, v in s.metrics.items() if k in ("seconds", "n_points", "n_grains", "peak_GB"))}
                for s in p.recipe().steps]
        cols = [{"name": k, "label": k, "field": k, "align": "left", "sortable": k in ("op", "map", "time")}
                for k in ("id", "op", "map", "backend", "stale", "time", "metrics")]
        tbl = ui.table(columns=cols, rows=rows, row_key="id", selection="single", pagination=20).classes("w-full").props("dense").mark("recipe_table")
        detail = ui.column().classes("w-full")

        def show_detail():
            detail.clear()
            if not tbl.selected:
                return
            s = p.recipe().step(tbl.selected[0]["id"])
            with detail:
                ui.label(f"{s.op} {s.id} — {s.backend or 'no backend'} {s.versions}").classes("font-bold")
                with ui.row().classes("w-full gap-4 no-wrap"):
                    with ui.column().classes("w-1/2"):
                        ui.label("parameters (edit and re-run as a new step)").classes("text-xs")
                        ed = ui.codemirror(json.dumps(s.params, indent=1, ensure_ascii=False), language="JSON").classes("w-full h-64").mark("step_params")

                        async def rerun():
                            try:
                                params = json.loads(ed.value)
                                st = await ng_run.io_bound(service.run, p, s.op, params)
                                ui.notify(f"{s.op} re-run as {st.id}", type="positive")
                                ui.navigate.to("/recipe")
                            except Exception as e:  # noqa: BLE001
                                error(e)
                        ui.button("Re-run with these parameters", on_click=rerun).props("dense").mark("rerun_step")
                    with ui.column().classes("w-1/2"):
                        ui.label("inputs").classes("text-xs")
                        for i in s.inputs:
                            ui.label(i if isinstance(i, str) else f"{i.path}").classes("text-xs break-all")
                        ui.label("outputs").classes("text-xs")
                        for k, r in s.outputs.items():
                            f = p.root / r.path
                            with ui.row().classes("items-center gap-1"):
                                ui.label(f"{k} ({r.kind}): {r.path}").classes("text-xs break-all")
                                ui.button(icon="download", on_click=lambda f=f: ui.download.file(f)).props("dense flat size=xs")
                            if r.kind == "png" and f.is_file():
                                ui.image(str(f)).classes("w-80")
                        ui.label("metrics: " + ", ".join(f"{k}={v}" for k, v in s.metrics.items())).classes("text-xs")
        tbl.on("selection", lambda _: show_detail())

        async def undo():
            lab = service.undo(p)
            ui.notify(f"undone: {lab}" if lab else "nothing to undo", type="positive" if lab else "warning")
            ui.navigate.to("/recipe")

        async def redo():
            lab = service.redo(p)
            ui.notify(f"redone: {lab}" if lab else "nothing to redo", type="positive" if lab else "warning")
            ui.navigate.to("/recipe")

        with ui.row().classes("items-end gap-2"):
            ui.button("Undo", icon="undo", on_click=undo).mark("undo")
            ui.button("Redo", icon="redo", on_click=redo).mark("redo")
            ui.separator().props("vertical")
            be = ui.select({"": "recorded backends", **{n: n for n, r in backend_status().items() if not r}},
                           value="", label="replay with").classes("w-48")
            stale = ui.checkbox("include stale steps")
            until = ui.checkbox("only up to the selected step")
            rep_out = ui.column().classes("w-full")

            async def do_replay():
                n = ui.notification("replaying ...", spinner=True, timeout=None)
                try:
                    u = tbl.selected[0]["id"] if until.value and tbl.selected else None
                    rep = await ng_run.io_bound(service.replay, p, be.value or None, None, stale.value, u)
                    rep_out.clear()
                    with rep_out:
                        ui.label(("replay reproduced all outputs" if rep.ok else "replay found differences")
                                 + f" — {rep.replay_root}").classes("font-bold " + ("text-green-700" if rep.ok else "text-orange-700")).mark("replay_result")
                        for c in rep.input_changes:
                            ui.label(c).classes("text-xs text-orange-700")
                        ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"}
                                          for k in ("step_id", "op", "output", "status", "detail")],
                                 rows=[c.model_dump() for c in rep.checks]).props("dense flat").classes("w-full")
                except Exception as e:  # noqa: BLE001
                    error(e)
                finally:
                    n.dismiss()
            ui.button("Replay", icon="replay", on_click=do_replay).mark("replay")


# ---- generic parameter panels -------------------------------------------------------------------------
def _unwrap(tp):
    """(base type, optional) of an annotation."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return tp, True
    return tp, False


def param_form(model: type[BaseModel], values: dict | None = None) -> callable:
    """Widgets for the fields of a pydantic params model; returns a function that collects the values.
    Simple fields get inputs / numbers / checkboxes / selects; nested fields (objects, lists, tuples) JSON text."""
    values = values or {}
    widgets = {}
    for name, f in model.model_fields.items():
        tp, optional = _unwrap(f.annotation)
        default = values.get(name, f.default if f.default is not None or optional else None)
        if callable(getattr(f, "default_factory", None)) and name not in values:
            default = f.default_factory()
        if isinstance(default, BaseModel):
            default = default.model_dump(mode="json")
        label = name + ("" if f.is_required() else "")
        origin = typing.get_origin(tp)
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            if origin is typing.Literal:
                opts = list(typing.get_args(tp)) + ([None] if optional else [])
                w = ui.select({o: str(o) for o in opts}, value=default if default in opts else opts[0], label=label).classes("w-64")
                kind = "raw"
            elif tp is bool:
                w = ui.checkbox(label, value=bool(default))
                kind = "raw"
            elif tp in (int, float):
                w = ui.number(label, value=default).classes("w-48")
                kind = "int" if tp is int else "float"
            elif tp is str:
                w = ui.input(label, value=default or "").classes("w-96")
                kind = "str_opt" if optional else "str"
            else:
                w = ui.textarea(label, value=json.dumps(default, ensure_ascii=False)).classes("w-full").props("autogrow dense")
                kind = "json"
            if f.description:
                ui.label(f.description).classes("text-xs text-gray-500")
        widgets[name] = (w, kind, optional)

    def collect() -> dict:
        out = {}
        for name, (w, kind, optional) in widgets.items():
            v = w.value
            if kind == "json":
                v = json.loads(v) if v and v.strip() else None
            elif kind == "int" and v is not None:
                v = int(v)
            elif kind == "float" and v is not None:
                v = float(v)
            elif kind == "str_opt" and v == "":
                v = None
            if v is None and not optional and model.model_fields[name].is_required():
                raise ValueError(f"{name} is required")
            if v is not None or optional:
                out[name] = v
        return out
    return collect


def ops_page(current_project, nav, error) -> None:
    nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-5xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        ui.label("Parameter panels for every operation (same as the MCP tools; each run is a recipe step)").classes("text-md font-bold")
        sel = ui.select(sorted(service.OPS), value="ebsd.cleanup", label="operation").classes("w-72").mark("ops_select")
        doc = ui.label("").classes("text-sm text-gray-700 whitespace-pre-wrap")
        form = ui.column().classes("w-full gap-1").mark("ops_form")
        out = ui.column().classes("w-full")
        state = {"collect": None}

        def build():
            spec = service.OPS[sel.value]
            doc.text = spec.doc + ("" if spec.uses_backend else "\n(backend-independent)")
            form.clear()
            vals = {}
            if "map_id" in spec.params.model_fields and p.samples():
                vals["map_id"] = p.samples()[0].map_id
            with form:
                state["collect"] = param_form(spec.params, vals)
        sel.on_value_change(lambda _: build())
        build()

        async def run_op():
            try:
                params = state["collect"]()
            except Exception as e:  # noqa: BLE001
                error(e)
                return
            n = ui.notification(f"{sel.value} ({p.info().backend}) ...", spinner=True, timeout=None)
            try:
                step = await ng_run.io_bound(service.run, p, sel.value, params)
                out.clear()
                with out:
                    ui.label(f"{sel.value} done: step {step.id}, {step.metrics}").classes("text-sm").mark("ops_result")
                    for k, r in step.outputs.items():
                        f = p.root / r.path
                        ui.label(f"{k}: {r.path}").classes("text-xs")
                        if r.kind == "png":
                            ui.image(str(f)).classes("w-full max-w-3xl")
                        elif r.kind == "json" and f.stat().st_size < 20000:
                            ui.code(f.read_text(encoding="utf-8"), language="json").classes("w-full")
            except Exception as e:  # noqa: BLE001
                error(e)
            finally:
                n.dismiss()
        ui.button("Run", icon="play_arrow", on_click=run_op).mark("ops_run")


# ---- settings -------------------------------------------------------------------------------------------
CMAPS = ["parula", "viridis", "turbo", "jet", "gray", "hsv", "hot", "cool"]


def settings_page(current_project, nav, error) -> None:
    nav()
    s = load_settings()
    status = backend_status()
    with ui.column().classes("p-4 gap-3 w-full max-w-4xl"):
        with ui.card().classes("w-full").mark("settings_backend"):
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
                ui.label(f"Project {p.name} uses {p.info().backend}. Changing it marks results of the other backend "
                         "stale (undoable).").classes("text-sm")

        with ui.card().classes("w-full").mark("settings_ipf"):
            ui.label("IPF colour key per phase (shared by both renderers)").classes("text-md font-bold")
            keys = {}
            for phase, k in s.figure.ipf.items():
                with ui.row().classes("items-center gap-2"):
                    ui.label(phase).classes("w-24")
                    kk = ui.select(["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"], value=k.key, label="key").classes("w-36")
                    dd = ui.select(["X", "Y", "Z", "build", "custom"], value=k.direction, label="reference direction").classes("w-40")
                    cv = [ui.number(c, value=v).classes("w-16") for c, v in zip("xyz", k.custom_vector or (0.0, 0.0, 1.0))]
                    kif = ui.checkbox("key in figure", value=k.key_in_figure)
                    keys[phase] = (kk, dd, cv, kif)
            ui.label("build = the build direction from the sample sheet; custom = vector in the map frame "
                     "(x right, y down, z = -out of screen as setting 2).").classes("text-xs text-gray-600")

        with ui.card().classes("w-full").mark("settings_style"):
            ui.label("Figure style").classes("text-md font-bold")
            with ui.row().classes("items-end gap-2"):
                cms = ui.select(CMAPS, value=s.figure.colormap_scalar, label="colour map CI/IQ/fit").classes("w-44")
                cmk = ui.select(CMAPS, value=s.figure.colormap_kam, label="colour map KAM").classes("w-40")
                lw = ui.number("boundary line width", value=s.figure.boundary_line_width).classes("w-40")
                bc = ui.input("boundary colour", value=s.figure.boundary_color).classes("w-32")
            with ui.row().classes("items-end gap-2"):
                sb = ui.checkbox("scale bar", value=s.figure.scale_bar)
                width = ui.number("figure width [cm]", value=s.figure.width_cm).classes("w-40")
                dpi = ui.number("DPI", value=s.figure.dpi).classes("w-24")
            ui.label("parula is a MATLAB colour map; pymtex draws viridis instead (the two renderers may differ "
                     "slightly).").classes("text-xs text-gray-600")

        with ui.card().classes("w-full").mark("settings_paths"):
            ui.label("Paths (this machine)").classes("text-md font-bold")
            tf = find_settings_file()
            ui.label(f"TOML: {tf or 'none found'}; preferences: {preferences_path()}").classes("text-xs text-gray-600")
            pw = {k: ui.input(k, value=getattr(s.paths, k)).classes("w-full").mark(k) for k in Paths.model_fields}

        def save():
            try:
                st = load_settings()
                st.backend = radio.value
                for phase, (kk, dd, cv, kif) in keys.items():
                    st.figure.ipf[phase] = IpfKeySetting(key=kk.value, direction=dd.value, key_in_figure=kif.value,
                                                         custom_vector=tuple(float(c.value or 0) for c in cv)
                                                         if dd.value == "custom" else None)
                st.figure = FigureStyle(ipf=st.figure.ipf, colormap_scalar=cms.value, colormap_kam=cmk.value,
                                        boundary_line_width=float(lw.value), boundary_color=bc.value,
                                        scale_bar=sb.value, width_cm=float(width.value), dpi=int(dpi.value))
                import tomllib  # noqa: PLC0415

                toml_paths = Paths(**(tomllib.loads(tf.read_text(encoding="utf-8")).get("paths", {}) if tf else {}))
                changed = {k: w.value for k, w in pw.items() if (w.value or "") != getattr(toml_paths, k)}
                path = save_preferences(st, paths=changed)
                msg = f"saved to {path}"
                pr = current_project()
                if pr:
                    n = service.switch_backend(pr, radio.value)
                    msg += f"; project backend {radio.value}" + (f", {n} steps marked stale" if n else "")
                ui.notify(msg, type="positive")
            except Exception as e:  # noqa: BLE001
                error(e)
        ui.button("Save settings", on_click=save).mark("save_settings")
