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


# ---- progress of a recipe run (replay, recipe file) ----------------------------------------------------------
async def run_with_progress(title: str, fn, n_hint: int | None = None):
    """Run fn(on_step, should_stop) in a worker thread with a progress dialog: "step 3 of 9: Grains and boundaries
    (pymtex)", a progress bar, the elapsed time and Cancel (stops before the next step; the running step finishes).
    Returns fn's result (None if it raised; the error is shown)."""
    import time  # noqa: PLC0415

    st = {"k": 0, "n": n_hint or 0, "label": "starting…", "stop": False, "t0": time.perf_counter()}

    def on_step(k, n, s):
        st.update(k=k, n=n, label=f"step {k + 1} of {n}: {OP_LABEL.get(s.op, s.op)}"
                                  + (f" ({s.backend})" if s.backend else ""))

    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-[30rem] max-w-full gap-2").mark("replay_dialog"):
        ui.label(title).classes("text-lg font-semibold")
        lab = ui.label(st["label"]).classes("text-sm").mark("replay_step")
        bar = ui.linear_progress(value=0, show_value=False).props("rounded color=primary").mark("replay_bar")
        with ui.row().classes("w-full items-center no-wrap"):
            el = ui.label("0 s").classes("text-xs ti-muted grow").mark("replay_elapsed")

            def cancel():
                st["stop"] = True
                cb.disable()
                cb.text = "Stopping after this step…"
            cb = ui.button("Cancel", icon="close", on_click=cancel).props("flat").mark("replay_cancel")

    def tick():
        lab.text = st["label"]
        bar.value = (st["k"] / st["n"]) if st["n"] else 0
        s = int(time.perf_counter() - st["t0"])
        el.text = f"elapsed {s // 60} min {s % 60:02d} s" if s >= 60 else f"elapsed {s} s"
    timer = ui.timer(0.3, tick)
    dlg.open()
    try:
        return await ng_run.io_bound(fn, on_step, lambda: st["stop"])
    except Exception as e:  # noqa: BLE001
        error(e, f"{title} failed")
        return None
    finally:
        timer.cancel()
        dlg.close()


def _report_text(rep) -> str:
    s = int(rep.seconds)
    t = f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{rep.seconds:.1f} s"
    return (f"{len(rep.steps)} step{'s' if len(rep.steps) != 1 else ''} in {t}"
            + (" (cancelled)" if rep.cancelled else ""))


# ---- History ------------------------------------------------------------------------------------------------
OP_LABEL = {"ebsd.load": "Load map", "ebsd.cleanup": "Cleanup", "grains.calc": "Grains and boundaries",
            "map.compute": "KAM / GROD / GOS / GND map", "figure": "Figure", "annotations.set": "Annotations",
            "recon.run": "Prior-β reconstruction", "recon.figure": "Reconstruction figure", "recon.sweep": "Parameter sweep",
            "grainsize.run": "Grain size", "grainsize.figure": "Grain-size figure",
            "grainsize.compare_oim": "Grain size vs OIM", "compare.oim": "Compare with OIM", "sample.set": "Map sheet",
            "ebsd.crop": "Crop", "compare.backends": "Compare backends", "texture.run": "Texture",
            "elastic.run": "Elastic properties (E from texture)"}
RECON_OPS = ("recon.run", "recon.figure", "recon.sweep")


def step_label(s, tab: str = "alpha") -> str:
    """Plain name of a step in History: the map kind of map.compute, prior-beta grains on the prior beta tab."""
    if s.op == "map.compute" and s.params.get("kind"):
        return f"{str(s.params['kind']).upper()} map"
    if s.op == "grains.calc" and tab == "beta":
        return "Prior-β grains and boundaries"
    if s.op == "ebsd.clean":
        from ..backends.base import CLEAN_LABELS  # noqa: PLC0415

        return "Cleanup: " + CLEAN_LABELS.get(s.params.get("operation"), str(s.params.get("operation")))
    return OP_LABEL.get(s.op, s.op)


def _local(ts: str) -> str:
    """Recipe timestamps are UTC: shown in local time (MM-DD HH:MM), as the section badges (fix 5)."""
    from datetime import datetime  # noqa: PLC0415

    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return ts[5:16].replace("T", " ")


def step_summary(p: Project, s) -> str:
    """The plain-language result line of a step (cleanup report summary), '' if it has none."""
    ref = s.outputs.get("report")
    if ref is None or ref.kind != "json":
        return ""
    try:
        import json  # noqa: PLC0415

        return json.loads((p.root / ref.path).read_text(encoding="utf-8")).get("summary") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def tab_steps(p: Project, map_id: str | None, tab: str, include_stale: bool = True) -> list:
    """Steps of one map shown in a map tab: 'beta' = the reconstruction and what uses it (its history starts at the
    reconstruction step); 'alpha' = everything else of the map."""
    from ..layers import RECON_KINDS  # noqa: PLC0415

    recon_ids = {s.id for s in p.recipe().steps if s.op == "recon.run"}

    def is_beta(s) -> bool:
        return (s.op in RECON_OPS or (s.op == "grains.calc" and s.params.get("source") in recon_ids)
                or (s.op == "figure" and s.params.get("kind") in RECON_KINDS)
                or (s.op == "grainsize.run" and s.params.get("target") == "prior_beta")
                or (s.op == "grainsize.figure" and str(s.params.get("kind", "")).startswith("beta")))
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
                 + ". Drag a step onto another to change the order; right-click a step to delete it.").classes("text-xs ti-muted").mark("history_info")
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            ui.button("Delete all steps…", icon="delete_sweep", on_click=lambda: confirm_delete(False)) \
                .props("flat dense no-caps size=sm color=negative").mark("delete_all_steps") \
                .tooltip("Remove the steps of this map tab from the recipe (asks first; Undo brings them back)")
            n_stale = len(steps) - len(live)
            if n_stale:
                ui.button(f"Delete stale steps ({n_stale})…", icon="layers_clear",
                          on_click=lambda: confirm_delete(True)) \
                    .props("flat dense no-caps size=sm").mark("delete_stale_steps") \
                    .tooltip("Remove the stale (crossed-out) steps of this map tab (asks first; Undo brings them back)")
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
                        lab = ui.label(step_label(s, tab) + (" (stale)" if s.stale else "")) \
                            .classes("text-sm truncate" + (" ti-muted line-through" if s.stale else ""))
                        summ = step_summary(p, s)
                        if summ:
                            lab.tooltip(summ)
                        ui.label(f"{s.id} · {s.backend or 'no backend'} · {_local(s.timestamp)}") \
                            .classes("text-xs ti-muted truncate")
                    with ui.context_menu().mark(f"hist_menu_{s.id}"):       # right-click (fix 3.13)
                        ui.menu_item("Show parameters", on_click=lambda s=s: select(s.id))
                        ui.separator()
                        ui.menu_item("Delete…", on_click=lambda s=s: delete_one(s.id)) \
                            .classes("text-negative").mark(f"hist_delete_{s.id}")
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
                rep = await run_with_progress(
                    "Replaying the recipe in a scratch copy",
                    lambda on_step, stop: service.replay(p, be.value or None, None, stale.value, None, on_step, stop))
                if rep is None:
                    return
                rep_out.clear()
                with rep_out:
                    other = sum(c.status == "other_backend" for c in rep.checks)
                    head = ("Replay cancelled" if rep.cancelled else
                            "Replay reproduced all outputs" if rep.ok and not other else
                            "Replay finished" if rep.ok else "Replay found differences")
                    ui.label(f"{head}: {_report_text(rep)}") \
                        .classes("font-semibold " + ("text-green-800" if rep.ok and not rep.cancelled
                                                     else "text-amber-800")).mark("replay_result")
                    if other:
                        ui.label(f"{other} output(s) come from another backend than recorded: not compared value by "
                                 "value (backends agree only within the conformance tolerances).") \
                            .classes("text-xs ti-muted")
                    for c in rep.input_changes:
                        ui.label(c).classes("text-xs text-amber-800")
                    bad = [c for c in rep.checks if c.status in ("different", "missing")]
                    for c in bad[:20]:
                        ui.label(f"{c.op} {c.output}: {c.status} {c.detail}").classes("text-xs")
                    slow = sorted(rep.steps, key=lambda r: -r.seconds)[:5]
                    if slow:
                        ui.label("slowest steps: " + ", ".join(f"{OP_LABEL.get(r.op, r.op)} {r.seconds:.1f} s"
                                                               for r in slow)).classes("text-xs ti-muted")
            ui.button("Replay", icon="replay", on_click=stable(do_replay)).props("outline dense").mark("replay")

            async def do_run_file():
                f = await dialogs.open_files("Recipe file to run into this project", "", [("Recipe", "*.json")],
                                             multiple=False)
                if not f:
                    return
                try:
                    steps_ = service.load_recipe_file(f[0])
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not read the recipe file")
                    return
                rec = Path(f[0]).parent if (Path(f[0]).parent / "project.json").is_file() else None
                rep = await run_with_progress(
                    "Running the recipe file",
                    lambda on_step, stop: service.run_recipe(p, steps_, be.value or None, stale.value, None, rec,
                                                             on_step=on_step, should_stop=stop))
                if rep is not None:
                    ui.notify(_report_text(rep) + ("" if rep.ok else " (some outputs differ)"),
                              type="positive" if rep.ok and not rep.cancelled else "warning")
                body.refresh()
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

    def confirm_delete(stale_only: bool):
        mid, tab = current_map(), current_tab()
        tab_name = ("prior-β map" if tab == "beta" else "α map") + f" of {mid}"
        shown = [s for s in tab_steps(p, mid, tab) if s.stale or not stale_only]
        if stale_only:
            shown = [s for s in shown if s.stale]
        everything = [s for s in p.recipe().steps if s.stale or not stale_only]
        with ui.dialog() as dlg, ui.card().classes("w-[32rem] max-w-full gap-2").mark("delete_dialog"):
            ui.label("Delete stale steps?" if stale_only else "Delete all steps?").classes("text-lg font-semibold")
            whole = ui.checkbox(f"the whole project (all maps, images and stitches: {len(everything)} "
                                f"{'stale ' if stale_only else ''}steps), not only the {tab_name}",
                                value=False).mark("delete_whole")
            info = ui.label().classes("text-sm").mark("delete_info")

            def plan():
                ids = [s.id for s in (everything if whole.value else shown)]
                return service.plan_delete(p, ids, only_unused=stale_only)

            def describe(_=None):
                pl = plan()
                n = len(pl["delete"])
                t = (f"{n} step{'s' if n != 1 else ''} will be removed from the recipe"
                     + (f" ({len(pl['dependents'])} of them use a deleted step)" if pl["dependents"] else "")
                     + (f"; {len(pl['kept'])} stale step(s) stay because current steps use them" if pl["kept"] else "")
                     + ". Their files stay in the project folder. Undo (Ctrl+Z or the undo button at the top of "
                       "History) brings them back.")
                info.text = t
                ok.set_enabled(n > 0)
            whole.on_value_change(describe)

            async def go():
                dlg.close()
                pl = plan()
                try:
                    res = service.delete_steps(p, pl["delete"], "delete stale steps" if stale_only
                                               else "delete all steps")
                    ui.notify(f"{len(res['delete'])} step(s) deleted; Undo brings them back.", type="positive")
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not delete the steps")
                body.refresh()
                if on_changed:
                    r = on_changed()
                    if hasattr(r, "__await__"):
                        await r
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat").mark("delete_cancel")
                ok = ui.button("Delete", icon="delete", on_click=stable(go)).props("color=negative") \
                    .mark("delete_ok")
            describe()
        dlg.open()

    async def after_change():
        body.refresh()
        if on_changed:
            r = on_changed()
            if hasattr(r, "__await__"):
                await r

    async def delete_one(sid: str):
        """Right-click > Delete: no dependents -> deleted at once (undoable); else ask: delete them too / keep them
        marked stale."""
        try:
            plan = service.plan_delete(p, [sid])
            s = p.recipe().step(sid)
        except KeyError:
            return
        name = OP_LABEL.get(s.op, s.op)
        if not plan["dependents"]:
            try:
                service.delete_step(p, sid)
            except Exception as e:  # noqa: BLE001
                error(e, "Could not delete the step")
                return
            ui.notify(f"'{name}' ({sid}) deleted. Undo (Ctrl+Z or the undo button at the top of History) brings it "
                      "back.", type="positive", multi_line=True)
            st["sel"] = None if st["sel"] == sid else st["sel"]
            await after_change()
            return
        deps = [p.recipe().step(i) for i in plan["dependents"]]
        with ui.dialog() as dlg, ui.card().classes("w-[32rem] max-w-full gap-2").mark("delete_one_dialog"):
            ui.label(f"Delete '{name}' ({sid})?").classes("text-lg font-semibold")
            ui.label(f"{len(deps)} later step{'s use' if len(deps) != 1 else ' uses'} its result:").classes("text-sm")
            with ui.column().classes("gap-0 pl-2"):
                for d in deps[:12]:
                    ui.label(f"• {OP_LABEL.get(d.op, d.op)} ({d.id})").classes("text-sm")
                if len(deps) > 12:
                    ui.label(f"… and {len(deps) - 12} more").classes("text-sm ti-muted")
            ui.label("Delete them too, or keep them marked stale (crossed out; their results stay but are no longer "
                     "current). Undo brings everything back.").classes("text-xs ti-muted")

            async def go(mode: str):
                dlg.close()
                try:
                    res = service.delete_step(p, sid, dependents=mode)
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not delete the step")
                    return
                ui.notify(f"'{name}' deleted" + (f" with {len(res['delete']) - 1} later step(s)" if mode == "delete"
                                                 else f"; {len(res['stale'])} later step(s) marked stale")
                          + ". Undo brings it back.", type="positive", multi_line=True)
                st["sel"] = None
                await after_change()
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat").mark("delete_one_cancel")
                ui.button("Keep them, mark stale", icon="block", on_click=stable(lambda: go("stale"))) \
                    .props("outline").mark("delete_one_stale")
                ui.button("Delete them too", icon="delete", on_click=stable(lambda: go("delete"))) \
                    .props("color=negative").mark("delete_one_all")
        dlg.open()

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
