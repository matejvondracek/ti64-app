"""EBSD tab panels: History (right of the maps: the steps of the map tab shown, undo / redo, drag-and-drop reorder
with dependency checks, re-run a step with edited parameters, replay, run a recipe file, recover an earlier recipe
version), Compare backends and parameter panels for any operation (advanced, in the edit menu)."""
from __future__ import annotations

import json
import types
import typing
from pathlib import Path

from nicegui import run as ng_run
from nicegui import ui
from pydantic import BaseModel

from .. import history, service
from ..project import Project
from ..recipe import Recipe, recipe_versions
from . import dialogs
from .shell import error
from .state import autosave, stable


# ---- History ------------------------------------------------------------------------------------------------
OP_LABEL = {"ebsd.load": "Load map", "ebsd.cleanup": "Cleanup", "grains.calc": "Grains and boundaries",
            "map.compute": "KAM / GND map", "figure": "Figure", "export.map": "Export", "annotations.set": "Annotations",
            "recon.run": "Prior-β reconstruction", "recon.figure": "Reconstruction figure", "recon.sweep": "Parameter sweep",
            "grainsize.run": "Grain size", "grainsize.figure": "Grain-size figure",
            "grainsize.compare_oim": "Grain size vs OIM", "compare.oim": "Compare with OIM", "sample.set": "Map sheet",
            "ebsd.crop": "Crop", "compare.backends": "Compare backends"}
RECON_OPS = ("recon.run", "recon.figure", "recon.sweep")


def tab_steps(p: Project, map_id: str | None, tab: str, include_stale: bool = True) -> list:
    """Steps of one map shown in a map tab: 'beta' = the reconstruction and what uses it (its history starts at the
    reconstruction step); 'alpha' = everything else of the map."""
    from ..layers import RECON_KINDS  # noqa: PLC0415

    def is_beta(s) -> bool:
        lay = s.params.get("layer") if isinstance(s.params.get("layer"), dict) else {}
        return (s.op in RECON_OPS or (s.op == "export.map" and lay.get("kind") in RECON_KINDS)
                or (s.op == "figure" and s.params.get("kind") in RECON_KINDS))
    out = [s for s in p.recipe().steps if map_id and s.params.get("map_id") == map_id and s.op != "sample.set"
           and (include_stale or not s.stale)]
    return [s for s in out if is_beta(s) == (tab == "beta")]


def history_section(p: Project, run_step, current_map=None, current_tab=None, on_changed=None):
    """History of the map shown in the current map tab: undo / redo, the steps (newest last; drag a step onto
    another to reorder; invalid orders are refused with the reason, a valid one re-runs from the first changed step
    as one undoable action), a step's parameters (re-run with changes), replay / recipe files / earlier versions.
    Returns the refresh function."""
    from .. import reorder as ro  # noqa: PLC0415

    box = ui.column().classes("w-full gap-2")
    st: dict = {"drag": None, "sel": None}
    current_map = current_map or (lambda: None)
    current_tab = current_tab or (lambda: "alpha")

    @ui.refreshable
    def body():
        hs = history.status(p)
        mid, tab = current_map(), current_tab()
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            ui.button(icon="undo", on_click=undo).props("outline round dense").tooltip(
                f"Undo: {hs['undo'][-1]}" if hs["undo"] else "Nothing to undo").mark("undo")
            ui.button(icon="redo", on_click=redo).props("outline round dense").tooltip(
                f"Redo: {hs['redo'][-1]}" if hs["redo"] else "Nothing to redo").mark("redo")
            ui.label(f"undo: {hs['undo'][-1] if hs['undo'] else '–'}").classes("text-xs ti-muted truncate") \
                .mark("undo_status")
        it = p.interrupted()
        if it:
            with ui.row().classes("w-full items-center gap-2 text-amber-800"):
                ui.label(f"interrupted: {it['op']} {it.get('params', {}).get('map_id', '')}").classes("text-sm")

                async def rerun_it():
                    p.clear_interrupted()
                    await run_step(it["op"], it["params"], f"re-run {it['op']}", it.get("backend"), with_map=False)
                    body.refresh()
                ui.button("Rerun", icon="replay", on_click=stable(rerun_it)).props("dense outline")
        steps = tab_steps(p, mid, tab)
        live = [s for s in steps if not s.stale]
        ui.label(("Prior-β map" if tab == "beta" else "α map") + f" {mid or ''}: {len(live)} step{'' if len(live) == 1 else 's'}"
                 + (f" (+ {len(steps) - len(live)} stale)" if len(steps) > len(live) else "")
                 + ". Drag a step onto another to change the order.").classes("text-xs ti-muted").mark("history_info")
        lst = ui.column().classes("w-full gap-0").mark("recipe_table")
        with lst:
            if not steps:
                ui.label("No steps yet." if tab == "alpha" else "The prior-β history starts with the reconstruction "
                         "(Prior-β reconstruction section, Apply).").classes("text-sm ti-muted")
            n = 0
            for s in steps:
                if not s.stale:
                    n += 1
                row = ui.row().classes("ti-row w-full items-center no-wrap gap-1" +
                                       (" selected" if st["sel"] == s.id else "")) \
                    .props(f"tabindex=0 draggable={'false' if s.stale else 'true'}").mark(f"hist_{s.id}")
                with row:
                    ui.icon("drag_indicator" if not s.stale else "block", size="18px").classes("ti-muted")
                    ui.label(str(n) if not s.stale else "–").classes("text-xs ti-muted w-4")
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(OP_LABEL.get(s.op, s.op) + (" (stale)" if s.stale else "")) \
                            .classes("text-sm truncate" + (" ti-muted line-through" if s.stale else ""))
                        ui.label(f"{s.id} · {s.backend or 'no backend'} · {s.timestamp[5:16].replace('T', ' ')}") \
                            .classes("text-xs ti-muted truncate")
                row.on("click", lambda s=s: select(s.id))
                row.on("keydown.enter", lambda s=s: select(s.id))
                if not s.stale:
                    row.on("dragstart", lambda s=s: st.update(drag=s.id))
                    row.on("dragover.prevent", lambda: None, throttle=0.5)
                    row.on("dragenter", js_handler="(e) => e.currentTarget.classList.add('drop-target')")
                    row.on("dragleave", js_handler="(e) => e.currentTarget.classList.remove('drop-target')")
                    row.on("drop", lambda s=s: dropped(s.id))
            if len(live) > 1:
                end = ui.row().classes("ti-row w-full items-center gap-1 text-xs ti-muted").mark("hist_end")
                with end:
                    ui.icon("vertical_align_bottom", size="16px")
                    ui.label("drop here to move a step to the end")
                end.on("dragover.prevent", lambda: None, throttle=0.5)
                end.on("dragenter", js_handler="(e) => e.currentTarget.classList.add('drop-target')")
                end.on("dragleave", js_handler="(e) => e.currentTarget.classList.remove('drop-target')")
                end.on("drop", lambda: dropped(None))
        detail = ui.column().classes("w-full gap-1")
        if st["sel"]:
            try:
                s = p.recipe().step(st["sel"])
            except KeyError:
                s = None
            if s is not None:
                with detail:
                    ui.label(f"{OP_LABEL.get(s.op, s.op)} ({s.op} {s.id}) — {s.backend or 'no backend'}") \
                        .classes("text-sm font-semibold")
                    ed = ui.codemirror(json.dumps(s.params, indent=1, ensure_ascii=False), language="JSON") \
                        .classes("w-full h-48").mark("step_params")

                    async def rerun():
                        try:
                            params = json.loads(ed.value)
                        except ValueError as e:
                            error(e, "Parameters are not valid JSON")
                            return
                        await run_step(s.op, params, f"re-run {s.op}", None, with_map=False)
                        body.refresh()
                    ui.button("Re-run with these parameters", icon="replay", on_click=stable(rerun)) \
                        .props("dense outline").mark("rerun_step")
                    for k, r in s.outputs.items():
                        f = p.root / r.path
                        with ui.row().classes("items-center gap-1 no-wrap w-full"):
                            ui.label(f"{k} ({r.kind})").classes("text-xs grow truncate").tooltip(str(f))
                            ui.button(icon="folder_open", on_click=lambda f=f: _reveal(f)) \
                                .props("dense flat round size=xs").tooltip("Show in Explorer")

        with ui.expansion("Replay and recipe files", icon="replay").classes("w-full"):
            from ..backends import backend_status  # noqa: PLC0415

            be = ui.select({"": "recorded backends", **{n: n for n, r in backend_status().items() if not r}},
                           value="", label="replay with").classes("w-full")
            stale = ui.checkbox("include stale steps")
            rep_out = ui.column().classes("w-full")

            async def do_replay():
                n = ui.notification("Replaying the recipe in a scratch copy…", spinner=True, timeout=None)
                try:
                    rep = await ng_run.io_bound(service.replay, p, be.value or None, None, stale.value, None)
                    rep_out.clear()
                    with rep_out:
                        ui.label("Replay reproduced all outputs" if rep.ok else "Replay found differences") \
                            .classes("font-semibold " + ("text-green-800" if rep.ok else "text-amber-800")) \
                            .mark("replay_result")
                        for c in rep.input_changes:
                            ui.label(c).classes("text-xs text-amber-800")
                        bad = [c for c in rep.checks if c.status in ("different", "missing")]
                        for c in bad[:20]:
                            ui.label(f"{c.op} {c.output}: {c.status} {c.detail}").classes("text-xs")
                except Exception as e:  # noqa: BLE001
                    error(e, "Replay failed")
                finally:
                    n.dismiss()
            ui.button("Replay", icon="replay", on_click=stable(do_replay)).props("outline dense").mark("replay")

            async def do_run_file():
                f = await dialogs.open_files("Recipe file to run into this project", "", [("Recipe", "*.json")],
                                             multiple=False)
                if not f:
                    return
                n = ui.notification("Running the recipe…", spinner=True, timeout=None)
                try:
                    steps_ = service.load_recipe_file(f[0])
                    rec = Path(f[0]).parent if (Path(f[0]).parent / "project.json").is_file() else None
                    rep = await ng_run.io_bound(service.run_recipe, p, steps_, be.value or None, stale.value, None,
                                                rec)
                    ui.notify(f"{len(rep.steps)} steps run" + ("" if rep.ok else " (some outputs differ)"),
                              type="positive" if rep.ok else "warning")
                    body.refresh()
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not run the recipe file")
                finally:
                    n.dismiss()
            ui.button("Run a recipe file…", icon="playlist_play", on_click=stable(do_run_file)) \
                .props("outline dense").mark("run_recipe")
            vers = recipe_versions(p.root)
            if vers:
                ui.label(f"Earlier recipe versions ({len(vers)} kept in .history/recipe/)").classes("text-xs ti-muted mt-2")
                vsel = ui.select({str(v): v.stem.replace("recipe_", "") for v in vers}, label="version") \
                    .classes("w-full")

                def restore():
                    if not vsel.value:
                        return
                    try:
                        r = Recipe.load(Path(vsel.value))
                        before = history.snapshot(p)
                        autosave(p.save_recipe, r)
                        history.commit(p, f"restore recipe version {Path(vsel.value).stem}", before)
                        ui.notify(f"Recipe restored ({len(r.steps)} steps); undo to go back.", type="positive")
                        body.refresh()
                    except Exception as e:  # noqa: BLE001
                        error(e, "Could not restore the recipe version")
                ui.button("Restore this version", icon="restore", on_click=restore).props("flat dense")

    def select(sid: str):
        st["sel"] = None if st["sel"] == sid else sid
        body.refresh()

    def dropped(target: str | None):
        src = st["drag"]
        st["drag"] = None
        mid, tab = current_map(), current_tab()
        if not src or src == target or not mid:
            return
        full = [s.id for s in ro.map_steps(p, mid)]
        shown = [s.id for s in tab_steps(p, mid, tab, include_stale=False)]
        if src not in shown:
            return
        order = [i for i in shown if i != src]
        order.insert(order.index(target) if target in order else len(order), src)
        slots = [k for k, i in enumerate(full) if i in shown]          # permute the shown steps among their slots
        new_full = list(full)
        for k, i in zip(slots, order):
            new_full[k] = i
        steps = {s.id: s for s in ro.map_steps(p, mid)}
        reason = ro.check_order([steps[i] for i in new_full])
        if reason:
            ui.notify(f"This order is not possible: {reason}.", type="warning", multi_line=True, timeout=8000)
            return
        first = next((k for k, (a, b) in enumerate(zip(full, new_full)) if a != b), None)
        if first is None:
            return
        confirm_reorder(mid, new_full, first, len(full) - first)

    def confirm_reorder(mid, new_full, first, n):
        with ui.dialog() as dlg, ui.card().classes("w-[30rem] max-w-full gap-2").mark("reorder_dialog"):
            ui.label("Change the order of the steps?").classes("text-lg font-semibold")
            ui.label(f"Step {first + 1} and the {n - 1} step(s) after it run again in the new order. Their old results "
                     "stay in the recipe, marked stale. Undo (Ctrl+Z) restores the previous order.").classes("text-sm")

            async def go():
                dlg.close()
                note = ui.notification("Re-running the steps in the new order…", spinner=True, timeout=None)
                try:
                    res = await ng_run.io_bound(ro.reorder, p, mid, new_full)
                    ui.notify(f"New order applied: {len(res['rerun'])} step(s) run again, {len(res['stale'])} old "
                              "result(s) marked stale.", type="positive")
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not apply the new order")
                finally:
                    note.dismiss()
                body.refresh()
                if on_changed:
                    r = on_changed()
                    if hasattr(r, "__await__"):
                        await r
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat").mark("reorder_cancel")
                ui.button("Re-run in the new order", icon="swap_vert", on_click=stable(go)).mark("reorder_ok")
        dlg.open()

    async def undo():
        lab = service.undo(p)
        ui.notify(f"undone: {lab}" if lab else "nothing to undo", type="positive" if lab else "warning")
        if lab:
            ui.navigate.reload()

    async def redo():
        lab = service.redo(p)
        ui.notify(f"redone: {lab}" if lab else "nothing to redo", type="positive" if lab else "warning")
        if lab:
            ui.navigate.reload()

    with box:
        body()
    return body.refresh


def _reveal(f: Path) -> None:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(f)])  # noqa: S603, S607
    except Exception as e:  # noqa: BLE001
        error(e)


# ---- generic parameter forms (any operation) -------------------------------------------------------------------
def _unwrap(tp):
    """(base type, optional) of an annotation."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return tp, True
    return tp, False


def param_form(model: type[BaseModel], values: dict | None = None):
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
        origin = typing.get_origin(tp)
        if origin is typing.Literal:
            opts = list(typing.get_args(tp)) + ([None] if optional else [])
            w = ui.select({o: str(o) for o in opts}, value=default if default in opts else opts[0], label=name) \
                .classes("w-full")
            kind = "raw"
        elif tp is bool:
            w = ui.checkbox(name, value=bool(default))
            kind = "raw"
        elif tp in (int, float):
            w = ui.number(name, value=default, step=1 if tp is int else 0.1).classes("w-full")
            kind = "int" if tp is int else "float"
        elif tp is str:
            w = ui.input(name, value=default or "").classes("w-full")
            kind = "str_opt" if optional else "str"
        else:
            w = ui.textarea(name, value=json.dumps(default, ensure_ascii=False)).classes("w-full") \
                .props("autogrow outlined dense")
            kind = "json"
        if f.description:
            w.tooltip(f.description)
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


def ops_section(p: Project, current_map, run_step, show_image) -> None:
    ui.label("Parameter panels for every operation (same as the MCP tools); each run is a recipe step.") \
        .classes("text-xs ti-muted")
    sel = ui.select(sorted(service.OPS), value="ebsd.cleanup", label="operation").classes("w-full").mark("ops_select")
    doc = ui.label("").classes("text-xs ti-muted whitespace-pre-wrap")
    form = ui.column().classes("w-full gap-1").mark("ops_form")
    out = ui.column().classes("w-full")
    state = {"collect": None}

    def build():
        spec = service.OPS[sel.value]
        doc.text = spec.doc + ("" if spec.uses_backend else "\n(backend-independent)")
        form.clear()
        vals = {"map_id": current_map()} if "map_id" in spec.params.model_fields and current_map() else {}
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
        step = await run_step(sel.value, params, sel.value, None, with_map=False)
        if step:
            out.clear()
            with out:
                ui.label(f"{sel.value} done: step {step.id}").classes("text-sm").mark("ops_result")
            for k, r in step.outputs.items():
                if r.kind == "png":
                    show_image(p.root / r.path, f"{sel.value}: {k}")
    ui.button("Run", icon="play_arrow", on_click=run_op).mark("ops_run")


# ---- compare backends --------------------------------------------------------------------------------------------
def compare_table_rows(table: dict, only_diff: bool = False) -> list[dict]:
    """Rows of a compare.backends table for display (numbers rounded, > 1 % flagged)."""
    def f(v):
        return f"{v:.6g}" if isinstance(v, float) else ("" if v is None else str(v))
    rows = []
    for r in table["rows"]:
        flag = (r["rel_diff"] or 0) > 0.01 or (r["rel_diff"] is None and not r["same"])
        if only_diff and r["same"]:
            continue
        rows.append({"key": r["key"], "a": f(r["a"]), "b": f(r["b"]), "abs_diff": f(r["abs_diff"]),
                     "rel_diff": "" if r["rel_diff"] is None else f"{100 * r['rel_diff']:.3g} %",
                     "flag": "> 1 %" if flag else ""})
    return rows


def compare_section(p: Project, current_map, show_block, run_step, status: dict) -> None:
    from ..ops_compare import comparable_ops  # noqa: PLC0415

    if any(status.values()):
        ui.label("Needs both backends: " + "; ".join(f"{k}: {v}" for k, v in status.items() if v)) \
            .classes("text-xs text-amber-800")
    ui.label("Runs one step on pymtex and on MATLAB in a scratch copy and shows the numbers and both figures side by "
             "side.").classes("text-xs ti-muted")
    sel = ui.select(comparable_ops(), value="recon.run", label="operation").classes("w-full").mark("cmp_op")
    fk = ui.input("figure kind (optional)").classes("w-full").mark("cmp_figkind")
    only = ui.checkbox("only differing values")
    form = ui.column().classes("w-full gap-1").mark("cmp_form")
    state = {"collect": None}

    def build():
        spec = service.OPS[sel.value]
        form.clear()
        with form:
            state["collect"] = param_form(spec.params, {"map_id": current_map()} if current_map() else {})
    sel.on_value_change(lambda _: build())
    build()

    async def do():
        try:
            params = state["collect"]()
        except Exception as e:  # noqa: BLE001
            error(e)
            return
        step = await run_step("compare.backends", {"op": sel.value, "params": params, "figure_kind": fk.value or None},
                              f"{sel.value} on both backends", None, with_map=False)
        if not step:
            return
        t = json.loads((p.root / step.outputs["table"].path).read_text(encoding="utf-8"))
        files = {k: str(p.root / r.path) for k, r in step.outputs.items()}

        def show():
            a, b = t["backends"]
            ui.label(f"{t['op']} on {t['map_id']}: {t['n_identical']}/{t['n_values']} values identical, max relative "
                     f"difference {100 * t['max_rel_diff']:.3g} %").classes("font-semibold").mark("cmp_result")
            cols = [{"name": "key", "label": "value", "field": "key", "align": "left"},
                    {"name": "a", "label": a, "field": "a"}, {"name": "b", "label": b, "field": "b"},
                    {"name": "abs_diff", "label": f"{b} - {a}", "field": "abs_diff"},
                    {"name": "rel_diff", "label": "rel.", "field": "rel_diff"},
                    {"name": "flag", "label": "", "field": "flag"}]
            ui.table(columns=cols, rows=compare_table_rows(t, only.value), pagination=25).props("dense flat") \
                .classes("w-full").mark("cmp_table")
            with ui.row().classes("w-full no-wrap gap-2"):
                for be in (a, b):
                    with ui.column().classes("w-1/2"):
                        ui.label(f"{be}: " + ", ".join(f"{k} {v}" for k, v in t["versions"][be].items())).classes("text-xs")
                        if files.get(f"figure_{be}"):
                            ui.image(files[f"figure_{be}"]).classes("w-full")
        show_block(show, "Compare backends")
    ui.button("Compare", icon="compare", on_click=do).mark("cmp_run")
