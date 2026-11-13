"""Files tab (docs/gui_mockups/03_files.jpg): Add / Remove file on the left, the file tree Project -> Sample -> files
(grouped by type and folder, JEOL .txt sidecars hidden) in the middle and the properties panel of the selected item
on the right: the SAMPLE sheet (entered once, inherited by its files) or the MAP sheet of a file (inherited values
greyed and labelled "from sample"; editing one overrides it for that file only) + the file's metadata.

Files are linked by absolute path, never copied; removing one removes it from the project only. Every edit is saved
immediately (atomic writes); a change that results depend on marks the map's results stale."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from nicegui import run as ng_run
from nicegui import ui

from ..project import Project
from ..sample import (missing_fields, BuildDirection, FourPointBending, MapInfo, MapPosition, SampleInfo, effective,
                      suggest_input_state, suggest_role, suggest_sample_id)
from . import dialogs, indexing
from .shell import check_interrupted, error, header
from .state import GuiState, autosave, stable
from .tips import tip

KIND_LABEL = {"ang": "EBSD maps", "image": "SEM images", "up2": "Pattern files (.up2)", "other": "Other files"}
KIND_ICON = {"ang": "grain", "image": "image", "up2": "grid_on", "other": "description"}
AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
ROLES = {"tension": "tension", "middle": "middle (neutral axis)", "compression": "compression"}


def open_in_tool(p: Project, gs: GuiState, m: MapInfo) -> None:
    """.ang -> EBSD tab; images, mosaics and .up2 pattern files -> View tab."""
    gs.set("selected_file", m.id)
    tab = "/ebsd" if m.kind == "ang" else "/view"
    if m.kind != "ang":
        gs.set("view_file", m.id)
    gs.set("tab", tab)
    ui.navigate.to(tab)


def files_page(sel: str | None = None) -> None:
    """sel = map ID to select (link 'Open map sheet' from a step that needs a sheet value)."""
    p = header("Files")
    if p is None:
        ui.navigate.to("/")
        return
    gs = GuiState(p)
    if sel and p.file(sel) is not None:
        gs.set("files_sel", ["file", sel])
        gs.set("selected_file", sel)
    gs.set("tab", "/files")
    check_interrupted(p)
    st: dict = {"sel": gs.get("files_sel"), "drag": None, "collapsed": set(gs.get("files_collapsed") or [])}

    rows: dict[tuple, ui.element] = {}            # tree rows by (kind, id): selection is shown without a redraw

    def select(kind: str, ident: str, redraw: bool = True) -> None:
        """Select a tree item and show its sheet. redraw=False (a click in the tree) only moves the highlight: a
        redraw would replace the row under the mouse, so a double click would never reach it."""
        prev = st["sel"]
        st["sel"] = [kind, ident]
        gs.set("files_sel", st["sel"])
        if kind == "file":
            gs.set("selected_file", ident)
        if redraw:
            tree.refresh()
        else:
            for key, on in ((tuple(prev) if prev else None, False), ((kind, ident), True)):
                el = rows.get(key) if key else None
                if el is not None and not el.is_deleted:
                    el.classes(add="selected") if on else el.classes(remove="selected")
                    el.props(f'aria-selected={"true" if on else "false"}')
        props.refresh()

    # ---- actions -------------------------------------------------------------------------------------------
    async def add_files():
        sel = st["sel"]
        target = sel[1] if sel and sel[0] == "sample" else None
        last = gs.get("last_folder") or ""
        paths = await dialogs.open_files("Add files to the project", last, dialogs.FILE_TYPES, all_first=True)
        if not paths:
            return
        try:
            added = autosave(p.add_files, paths, target)
        except Exception as e:  # noqa: BLE001
            error(e, "Could not add the files")
            return
        gs.set("last_folder", str(Path(paths[0]).parent))
        from ..io import prias  # noqa: PLC0415

        n_prias = sum(1 for x in paths if prias.is_prias_file(x) and prias.ang_of(x) is not None)
        skipped = len(paths) - len(added) - n_prias
        with_prias = [m for m in added if m.kind == "ang" and prias.find_files(m.file)]
        ui.notify(f"{len(added)} file(s) linked" + (f" to {target}" if target else "")
                  + (f"; {skipped} JEOL .txt sidecar(s) shown with their images" if skipped > 0 else "")
                  + (f"; {n_prias} PRIAS export(s) used with their .ang" if n_prias else "")
                  + (f"; PRIAS found next to {len(with_prias)} .ang map(s) (map sheet, section 'PRIAS')"
                     if with_prias else ""), type="positive", multi_line=True)
        if added:
            select("file", added[-1].id)
            indexing.ask_pending(p, on_done=lambda: (tree.refresh(), props.refresh()), only=[m.id for m in added])
        unassigned = [m for m in p.files() if m.sample_id is None and suggest_sample_id(m.file)]
        if unassigned and target is None:
            ui.notify("Tip: Auto-assign suggests the sample of each file from its name.", type="info")

    def remove_selected():
        sel = st["sel"]
        if not sel:
            ui.notify("Select a file or a sample first.", type="warning")
            return
        if sel[0] == "file":
            m = p.file(sel[1])
            if m is None:
                return
            confirm(f"Remove {Path(m.file).name} from the project?",
                    "The file stays on disk; only the link and its map sheet are removed. Results already "
                    "computed from it stay in the recipe.",
                    lambda: (autosave(p.remove_file, m.id), st.update(sel=None), gs.set("files_sel", None),
                             tree.refresh(), props.refresh()), "Remove from project")
        else:
            n = sum(1 for m in p.files() if m.sample_id == sel[1])
            confirm(f"Delete sample {sel[1]}?", f"Its {n} file(s) move to Unassigned (nothing is deleted from disk).",
                    lambda: (autosave(p.delete_sample, sel[1]), st.update(sel=None), tree.refresh(), props.refresh()),
                    "Delete sample")

    def new_sample(prefill: str = "", file_id: str | None = None):
        """New sample; with file_id (right-click 'New sample for this file') the file moves into it."""
        f = p.file(file_id) if file_id else None
        with ui.dialog() as dlg, ui.card().classes("w-96 gap-3").mark("new_sample_dialog"):
            ui.label("New sample" + (f" for {Path(f.file).name}" if f else "")).classes("text-lg font-semibold")
            ui.label("A sample is one specimen; its sheet (material, 4PB geometry, build direction) is entered once "
                     "and inherited by its files." + (" The file moves into the new sample." if f else "")) \
                .classes("text-sm ti-muted")
            sid = ui.input("Sample ID (e.g. JU04)", value=prefill).classes("w-full").props("autofocus") \
                .tooltip(tip("sample_id")).mark("ns_id")

            def go():
                try:
                    s = SampleInfo(id=(sid.value or "").strip())
                    if p.sample_info(s.id):
                        raise ValueError(f"sample {s.id} exists already")
                    autosave(p.save_sample_info, s)
                    dlg.close()
                    if f is not None:
                        autosave(p.assign, [f.id], s.id)
                        ui.notify(f"{Path(f.file).name} → new sample {s.id}", type="positive")
                        select("file", f.id)
                    else:
                        select("sample", s.id)
                except Exception as e:  # noqa: BLE001
                    error(e, "Sample not created")
            sid.on("keydown.enter", go)
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Create", icon="check", on_click=go).mark("ns_create")
        dlg.open()

    def move(ids: list[str], sample_id: str | None):
        try:
            autosave(p.assign, ids, sample_id)
            ui.notify(f"{len(ids)} file(s) → {sample_id or 'Unassigned'}", type="positive")
        except Exception as e:  # noqa: BLE001
            error(e)
        tree.refresh()
        props.refresh()

    def auto_assign():
        files = [m for m in p.files() if m.sample_id is None] or p.files()
        rows = [(m, suggest_sample_id(m.file)) for m in files]
        with ui.dialog() as dlg, ui.card().classes("w-[44rem] max-w-full gap-2"):
            ui.label("Auto-assign files to samples").classes("text-lg font-semibold")
            ui.label("Suggested from the file or folder name. Check the list, change any sample ID, then confirm. "
                     "Missing samples are created.").classes("text-sm ti-muted")
            boxes = []
            with ui.scroll_area().classes("w-full").style("height: min(50vh, 420px)"):
                for m, sug in rows:
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        cb = ui.checkbox(value=bool(sug))
                        ui.label(Path(m.file).name).classes("grow truncate").tooltip(m.file)
                        inp = ui.input(value=sug or "", placeholder="no suggestion").classes("w-36") \
                            .props("dense").mark(f"aa_{m.id}")
                        boxes.append((m, cb, inp))

            def go():
                n = 0
                try:
                    for m, cb, inp in boxes:
                        sid = (inp.value or "").strip()
                        if cb.value and sid:
                            if p.sample_info(sid) is None:
                                autosave(p.save_sample_info, SampleInfo(id=sid))
                            autosave(p.assign, [m.id], sid)
                            n += 1
                except Exception as e:  # noqa: BLE001
                    error(e)
                dlg.close()
                ui.notify(f"{n} file(s) assigned", type="positive")
                tree.refresh()
                props.refresh()
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Assign checked files", icon="done_all", on_click=go).mark("aa_confirm")
        dlg.open()

    async def relocate(m: MapInfo | None = None):
        """Choose a folder; it is searched recursively by file name (and size) for ALL missing files, the matches
        are shown and relinked at once."""
        start = str(Path(m.file).parent) if m else (gs.get("last_folder") or "")
        while start and not Path(start).is_dir():
            start = str(Path(start).parent) if Path(start).parent != Path(start) else ""
        folder = await dialogs.pick_folder("Folder to search for the missing files (subfolders included)", start)
        if not folder:
            return
        n = ui.notification(f"Searching {folder}…", spinner=True, timeout=None)
        try:
            found = await ng_run.io_bound(p.find_missing, folder)
        except Exception as e:  # noqa: BLE001
            error(e, "Search failed")
            return
        finally:
            n.dismiss()
        relocate_result(folder, found)

    def relocate_result(folder: str, found: dict[str, list[str]]):
        files = {m.id: m for m in p.files()}
        with ui.dialog() as dlg, ui.card().classes("w-[46rem] max-w-full gap-2").mark("relocate_dialog"):
            ok = {k: v for k, v in found.items() if v}
            ui.label(f"Missing files found in {folder}: {len(ok)} of {len(found)}").classes("text-lg font-semibold")
            ui.label("Searched by file name in the folder and all its subfolders; when a name occurs more than once, "
                     "the file with the recorded size is proposed.").classes("text-sm ti-muted")
            choice: dict = {}
            with ui.column().classes("w-full gap-1 no-wrap").style("max-height: min(50vh, 380px); overflow-y: auto; "
                                                                   "overflow-x: hidden"):
                for mid, cands in found.items():
                    m = files.get(mid)
                    if m is None:
                        continue
                    with ui.row().classes("w-full items-center no-wrap gap-2 min-w-0"):
                        ui.icon("check_circle" if cands else "help", color="positive" if cands else "warning")
                        ui.label(Path(m.file).name).classes("truncate shrink-0").style("width: 13rem") \
                            .tooltip(f"was: {m.file}")
                        if cands:
                            # shown relative to the searched folder: the subfolder tells the candidates apart
                            choice[mid] = ui.select({c: _short(c, folder) for c in cands}, value=cands[0]) \
                                .classes("min-w-0").style("flex: 1 1 0").props("dense options-dense") \
                                .tooltip(cands[0]).mark(f"reloc_{mid}")
                        else:
                            ui.label("not found in this folder").classes("text-sm ti-muted grow")

            def go():
                done = 0
                try:
                    for mid, w in choice.items():
                        autosave(p.relocate, mid, w.value)
                        done += 1
                except Exception as e:  # noqa: BLE001
                    error(e, "Could not relink")
                dlg.close()
                ui.notify(f"{done} file(s) relinked", type="positive" if done else "warning")
                tree.refresh()
                props.refresh()
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button(f"Relink {len(choice)} file(s)", icon="link", on_click=go) \
                    .props("" if choice else "disable").mark("reloc_go")
        dlg.open()

    # handlers that await a Windows dialog run in the page's root slot: the list they were clicked in may be redrawn
    # while the dialog is open (stable(), state.py)
    add_files, relocate, remove_selected = stable(add_files), stable(relocate), stable(remove_selected)
    new_sample, auto_assign, move = stable(new_sample), stable(auto_assign), stable(move)

    # ---- layout --------------------------------------------------------------------------------------------
    with ui.row().classes("w-full no-wrap gap-3 p-3 items-stretch").style("height: calc(100vh - 56px)"):
        with ui.column().classes("gap-2 shrink-0").style("width: 168px"):
            ui.button("Add file", icon="note_add", on_click=add_files).classes("w-full").mark("add_file") \
                .tooltip("Link files (multi-select). They are never copied.")
            ui.button("Remove file", icon="link_off", on_click=remove_selected).props("outline") \
                .classes("w-full").mark("remove_file").tooltip("Remove the selected file or sample from the project "
                                                               "(never from disk)")
            ui.separator()
            ui.button("New sample", icon="science", on_click=lambda: new_sample()).props("outline") \
                .classes("w-full").mark("new_sample")
            ui.button("Auto-assign", icon="auto_fix_high", on_click=auto_assign).props("outline") \
                .classes("w-full").mark("auto_assign").tooltip("Suggest each file's sample from its name or folder")
            reloc_all = ui.button("Find missing…", icon="travel_explore", on_click=lambda: relocate()) \
                .props("outline color=negative").classes("w-full").mark("relocate_all") \
                .tooltip("Choose a folder: it is searched (with subfolders) for every missing file, by name and size")
            ui.label("Drag files onto a sample, or right-click a file to move it. Click a sample's name to fold its "
                     "files.").classes("text-xs ti-muted mt-2")
        with ui.column().classes("ti-card grow p-2 gap-0 overflow-auto").style("flex: 1 1 40%; min-width: 300px") \
                .mark("file_tree"):
            tree_box = ui.column().classes("w-full gap-0")
        with ui.column().classes("ti-card p-3 gap-2 overflow-auto").style("flex: 1 1 52%; min-width: 360px") \
                .mark("props_panel"):
            props_box = ui.column().classes("w-full gap-2")

    # ---- tree ----------------------------------------------------------------------------------------------
    def drop_zone(el, sample_id: str | None):
        el.on("dragover.prevent", lambda: None, throttle=0.5)
        el.on("dragenter", js_handler="(e) => e.currentTarget.classList.add('drop-target')")
        el.on("dragleave", js_handler="(e) => e.currentTarget.classList.remove('drop-target')")

        def dropped():
            if st["drag"]:
                move([st["drag"]], sample_id)
                st["drag"] = None
        el.on("drop", dropped)

    def file_row(m: MapInfo, samples: list[SampleInfo]):
        status = Project.file_status(m)
        sel = st["sel"] == ["file", m.id]
        row = ui.row().classes("ti-row w-full items-center no-wrap gap-2"
                               + (" selected" if sel else "") + (" missing" if status == "missing" else "")) \
            .props(f'draggable=true tabindex=0 role=treeitem aria-selected={"true" if sel else "false"}') \
            .mark(f"file_{m.id}")
        with row:
            ui.icon(KIND_ICON.get(m.kind, "description"), size="20px").classes(
                "text-gray-400" if status == "missing" else "text-primary")
            ui.label(Path(m.file).name).classes("ti-name truncate").tooltip(m.file)
            indexing.tag(m)
            ui.element("div").classes("grow")
            if status == "missing":
                ui.button("Relocate", icon="travel_explore", on_click=lambda m=m: relocate(m)) \
                    .props("dense flat size=sm color=negative").mark(f"relocate_{m.id}")
            elif status == "changed":
                ui.icon("warning", color="warning").tooltip("This file changed on disk since it was linked or last "
                                                            "used; results may not match it any more.")
            if m.kind == "ang" and (m.input_state is None or m.build_direction is None or m.sample_id is None):
                ui.icon("edit_note", color="warning").tooltip(
                    "Map sheet not complete: " + ", ".join(missing_fields(m)) + ". The map loads without them; "
                    "steps that need a value (e.g. the build direction) ask for it.")
            with ui.context_menu():
                ui.menu_item("Open", on_click=lambda m=m: open_in_tool(p, gs, m))
                for s in samples:
                    if s.id != m.sample_id:
                        ui.menu_item(f"Move to sample {s.id}", on_click=lambda m=m, s=s: move([m.id], s.id)) \
                            .mark(f"moveto_{m.id}_{s.id}")
                if m.sample_id:
                    ui.menu_item("Move to Unassigned", on_click=lambda m=m: move([m.id], None))
                ui.menu_item("New sample for this file…",
                             on_click=lambda m=m: new_sample(suggest_sample_id(m.file) or "", m.id)) \
                    .mark(f"newsample_{m.id}")
                if status == "missing":
                    ui.menu_item("Find missing files…", on_click=lambda m=m: relocate(m))
                if m.kind == "ang":
                    ui.separator()
                    ui.label("Indexing method").classes("text-xs ti-muted px-4 pt-1")
                    for v, lab in indexing.OPTIONS.items():
                        ui.menu_item(("✓ " if (m.indexing or "") == v else "    ") + lab,
                                     on_click=lambda m=m, v=v: set_indexing(m, v)).mark(f"indexing_{v or 'none'}_{m.id}")
                ui.separator()
                ui.menu_item("Remove from project", on_click=lambda m=m: (select("file", m.id), remove_selected()))
        # clicks on a button inside the row (Relocate) do not select the row: selecting redraws the list
        rows[("file", m.id)] = row
        row.on("click", lambda m=m: select("file", m.id, redraw=False),
               js_handler="(e) => { if (!e.target.closest('button')) emit(); }")
        row.on("dblclick", lambda m=m: open_in_tool(p, gs, m))
        row.on("keydown.enter", lambda m=m: open_in_tool(p, gs, m))
        row.on("keydown.space.prevent", lambda m=m: select("file", m.id, redraw=False))
        row.on("keydown.delete", lambda m=m: (select("file", m.id), remove_selected()))
        row.on("dragstart", lambda m=m: st.update(drag=m.id))

    def set_indexing(m: MapInfo, v: str) -> None:
        indexing.set_label(p, m.id, v or None)
        tree.refresh()
        props.refresh()

    def group_files(files: list[MapInfo], samples):
        by_kind: dict[str, list[MapInfo]] = defaultdict(list)
        for m in files:
            by_kind[m.kind].append(m)
        for kind in ("ang", "image", "up2", "other"):
            items = sorted(by_kind.get(kind, []), key=lambda m: (str(Path(m.file).parent).lower(), Path(m.file).name.lower()))
            if not items:
                continue
            # one level under the sample name, smaller and lighter than it; the files one more level (fix 5)
            ui.label(f"{KIND_LABEL[kind]} ({len(items)})").classes("ti-group").mark(f"group_{kind}")
            folders = sorted({str(Path(m.file).parent) for m in items})
            for folder in folders:
                if len(folders) > 1:
                    ui.label(folder).classes("ti-folder truncate").tooltip(folder)
                with ui.column().classes("w-full gap-0").style("padding-left: 92px"):
                    for m in items:
                        if str(Path(m.file).parent) == folder:
                            file_row(m, samples)

    def toggle(key: str) -> None:
        """Fold / unfold the files of a sample (remembered in gui_state.json)."""
        st["collapsed"].symmetric_difference_update({key})
        gs.set("files_collapsed", sorted(st["collapsed"]))
        tree.refresh()

    def sample_head(key: str, sel: bool, icon: str, icon_cls: str, title: str, sub: str, mark: str, on_select):
        folded = key in st["collapsed"]
        head = ui.row().classes("ti-row w-full items-center no-wrap gap-1 pl-1" + (" selected" if sel else "")) \
            .props(f'tabindex=0 role=treeitem aria-expanded={"false" if folded else "true"}').mark(mark)
        with head:
            ui.button(icon="chevron_right" if folded else "expand_more", on_click=lambda: toggle(key)) \
                .props("flat round dense size=sm").mark(f"fold_{mark}") \
                .tooltip("Show the files" if folded else "Hide the files")
            ui.icon(icon, size="22px").classes(icon_cls)
            name = ui.label(title).classes("font-semibold cursor-pointer").mark(f"name_{mark}") \
                .tooltip("Click to fold / unfold its files")
            name.on("click", lambda: toggle(key))
            ui.label(sub).classes("text-xs ti-muted")
        rows[("sample", key)] = head
        head.on("click", on_select, js_handler="(e) => { if (!e.target.closest('button')) emit(); }")
        head.on("keydown.enter", on_select)
        head.on("keydown.left", lambda: key not in st["collapsed"] and toggle(key))
        head.on("keydown.right", lambda: key in st["collapsed"] and toggle(key))
        return head, folded

    @ui.refreshable
    def tree():
        rows.clear()
        samples = sorted(p.sample_infos(), key=lambda s: s.id)
        files = p.files()
        reloc_all.set_visibility(any(Project.file_status(m) == "missing" for m in files))
        with ui.column().classes("w-full gap-0"):
            with ui.row().classes("items-center gap-2 px-2 py-1"):
                ui.icon("folder_special", size="22px").classes("text-secondary")
                ui.label(p.info().name).classes("font-semibold")
                ui.label(f"{len(files)} files, {len(samples)} samples").classes("text-xs ti-muted")
            if not files and not samples:
                ui.label("No files yet. Click 'Add file' to link EBSD maps (.ang), SEM images (.bmp / .tif) or "
                         ".up2 pattern files.").classes("text-sm ti-muted p-4").mark("tree_empty")
            for s in samples:
                sel = st["sel"] == ["sample", s.id]
                mine = [m for m in files if m.sample_id == s.id]
                head, folded = sample_head(
                    s.id, sel, "science", "text-accent", s.id,
                    f"{len(mine)} file(s)" + (f" · {s.material}" if s.material else ""), f"sample_{s.id}",
                    lambda s=s: select("sample", s.id, redraw=False))
                drop_zone(head, s.id)
                if not folded:
                    group_files(mine, samples)
            un = [m for m in files if m.sample_id is None]
            head, folded = sample_head("__unassigned", False, "inbox", "text-gray-500", "Unassigned",
                                       f"{len(un)} file(s)", "sample__unassigned", lambda: None)
            drop_zone(head, None)
            if not folded:
                group_files(un, samples)
        # keep the selection valid
        if st["sel"] and st["sel"][0] == "file" and p.file(st["sel"][1]) is None:
            st["sel"] = None

    # ---- properties ----------------------------------------------------------------------------------------
    @ui.refreshable
    def props():
        with ui.column().classes("w-full gap-2"):
            sel = st["sel"]
            if not sel:
                ui.label("Select a sample or a file to see its sheet.").classes("ti-muted")
                return
            if sel[0] == "sample":
                s = p.sample_info(sel[1])
                if s is None:
                    ui.label("Sample not found.").classes("ti-muted")
                    return
                sample_sheet(p, s, on_rename=lambda new: select("sample", new), on_saved=tree.refresh)
            else:
                m = p.file(sel[1])
                if m is None:
                    ui.label("File not found.").classes("ti-muted")
                    return
                map_sheet(p, gs, m, on_saved=tree.refresh, open_tool=lambda: open_in_tool(p, gs, m),
                          relocate=lambda: relocate(m))

    with tree_box:
        tree()
    with props_box:
        props()
    # fix 5: maps that look re-indexed (also those already in the project) are asked about once
    ui.timer(0.3, lambda: indexing.ask_pending(p, on_done=lambda: (tree.refresh(), props.refresh())), once=True)


def _short(path: str, folder: str) -> str:
    """A found file relative to the searched folder (…\\sub\\name.bmp)."""
    try:
        return "…\\" + str(Path(path).relative_to(folder))
    except ValueError:
        return path


def confirm(title: str, text: str, action, ok_label: str = "OK") -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[28rem] max-w-full gap-2"):
        ui.label(title).classes("text-lg font-semibold")
        ui.label(text).classes("text-sm")

        def go():
            dlg.close()
            try:
                action()
            except Exception as e:  # noqa: BLE001
                error(e)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat").mark("confirm_cancel")
            ui.button(ok_label, on_click=go).props("color=negative").mark("confirm_ok")
    dlg.open()


def _num(label: str, value, key: str, step: float = 1.0, **kw):
    return ui.number(label, value=value, step=step, **kw).classes("w-36").tooltip(tip(key))


# ---- sample sheet ----------------------------------------------------------------------------------------------
def sample_sheet(p: Project, s: SampleInfo, on_rename, on_saved) -> None:
    files = [m for m in p.files() if m.sample_id == s.id]
    ui.label(f"Sample {s.id}").classes("text-lg font-semibold").mark("sheet_sample")
    ui.label(f"Specimen data, entered once and inherited by its {len(files)} file(s). Saved as you type.") \
        .classes("text-xs ti-muted")
    msg = ui.label().classes("text-sm text-amber-800")
    state = {"id": s.id}

    def save(**changes):
        try:
            new = s.model_copy(update=changes)
            new = SampleInfo.model_validate(new.model_dump())
            autosave(p.save_sample_info, new, state["id"])
            msg.text = ""
            for k, v in changes.items():
                setattr(s, k, v)
            on_saved()
        except Exception as e:  # noqa: BLE001
            msg.text = "Not saved: " + str(e).splitlines()[-1][:200]

    sid = ui.input("Specimen ID", value=s.id).classes("w-48").tooltip(tip("sample_id")).mark("ss_id")

    def rename():
        new = (sid.value or "").strip()
        if new and new != state["id"]:
            try:
                autosave(p.save_sample_info, SampleInfo.model_validate({**s.model_dump(), "id": new}), state["id"])
                state["id"] = new
                on_rename(new)
            except Exception as e:  # noqa: BLE001
                msg.text = "Not saved: " + str(e)
    sid.on("blur", rename)
    sid.on("keydown.enter", rename)
    with ui.row().classes("gap-2 w-full"):
        for k, lab in (("material", "Material"), ("process", "Process"), ("heat_treatment", "Heat treatment")):
            w = ui.input(lab, value=getattr(s, k)).classes("w-48").tooltip(tip(k)).mark(f"ss_{k}")
            w.on("blur", lambda e, k=k, w=w: save(**{k: w.value or ""}))
    ui.label("4-point bending (all four sizes or none)").classes("font-semibold mt-2").tooltip(tip("bending"))
    b = s.bending
    bw = {}
    with ui.row().classes("gap-2"):
        for k, lab in (("outer_span_mm", "outer span [mm]"), ("inner_span_mm", "inner span [mm]"),
                       ("width_mm", "width [mm]"), ("thickness_mm", "thickness [mm]"), ("load_N", "load [N]")):
            bw[k] = _num(lab, getattr(b, k) if b else None, "bending", step=0.5 if k != "load_N" else 10,
                         min=0).mark(f"ss_{k}")

    def save_bending(_=None):
        vals = {k: w.value for k, w in bw.items()}
        geo = [vals[k] for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm")]
        if all(v is None for v in geo) and vals["load_N"] is None:
            save(bending=None)
        elif any(v is None for v in geo):
            msg.text = "4PB not saved yet: enter outer span, inner span, width and thickness."
        else:
            try:
                save(bending=FourPointBending(**{k: v for k, v in vals.items() if v is not None}))
            except Exception as e:  # noqa: BLE001
                msg.text = "Not saved: " + str(e).splitlines()[-1][:200]
    for w in bw.values():
        w.on("blur", save_bending)
    with ui.row().classes("gap-2 items-end mt-2"):
        bd = ui.select({None: "not set", **{a: a for a in AXES}}, value=s.build_direction_specimen,
                       label="Build direction (specimen frame)").classes("w-56").tooltip(tip("bd_specimen")) \
            .mark("ss_bd")
        bd.on_value_change(lambda e: save(build_direction_specimen=e.value))
        ts = ui.select({None: "not set", "+Y": "+Y face", "-Y": "-Y face"}, value=s.tension_side,
                       label="Tension side").classes("w-40").tooltip(tip("tension_side")).mark("ss_tension")
        ts.on_value_change(lambda e: save(tension_side=e.value))
    notes = ui.textarea("Notes", value=s.notes).classes("w-full").props("autogrow outlined").mark("ss_notes")
    notes.on("blur", lambda: save(notes=notes.value or ""))
    if files:
        ui.label("Files").classes("font-semibold mt-2")
        for m in files:
            ui.label(f"• {Path(m.file).name}").classes("text-sm")


# ---- map sheet --------------------------------------------------------------------------------------------------
def map_sheet(p: Project, gs: GuiState, m: MapInfo, on_saved, open_tool, relocate) -> None:
    s = p.sample_info(m.sample_id) if m.sample_id else None
    status = Project.file_status(m)
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        ui.icon(KIND_ICON.get(m.kind, "description"), size="26px").classes("text-primary")
        with ui.column().classes("gap-0 grow min-w-0 overflow-hidden"):
            with ui.row().classes("items-center no-wrap gap-2 min-w-0"):
                ui.label(f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}") \
                    .classes("text-lg font-semibold truncate").mark("sheet_map")
                indexing.tag(m)
            ui.label(m.file).classes("text-xs ti-muted break-all")
        label = {"ang": "Open in EBSD tab", "image": "Open in View tab", "up2": "Open in View tab"}.get(m.kind)
        if label and status != "missing":
            ui.button(label, icon="open_in_new", on_click=open_tool).classes("shrink-0 whitespace-nowrap")                 .mark("open_tool")
    if status == "missing":
        with ui.row().classes("items-center gap-2 text-red-800"):
            ui.icon("link_off")
            ui.label("The linked file is missing (moved or renamed).")
            ui.button("Find missing files…", icon="travel_explore", on_click=relocate).props("outline")                 .tooltip("Choose a folder: it is searched (with subfolders) for every missing file")
    elif status == "changed":
        ui.label("This file changed on disk since it was linked or last used; results may not match it.") \
            .classes("text-sm text-amber-800")
    msg = ui.label().classes("text-sm text-amber-800")

    def save(**changes):
        try:
            nm = autosave(p.update_file, m.id, **changes)
            for k in changes:
                setattr(m, k, getattr(nm, k))
            msg.text = ""
            on_saved()
            return True
        except Exception as e:  # noqa: BLE001
            msg.text = "Not saved: " + str(e).splitlines()[-1][:240]
            return False

    if m.kind in ("ang", "image"):
        _map_fields(p, m, s, save, msg)
    _inherited_fields(m, s, save, msg)
    notes = ui.textarea("Notes (this file)", value=m.notes).classes("w-full").props("autogrow outlined")
    notes.on("blur", lambda: save(notes=notes.value or ""))
    ui.separator()
    _metadata(p, m)


def _map_fields(p: Project, m: MapInfo, s: SampleInfo | None, save, msg) -> None:
    if m.kind == "ang":
        ui.label("Map sheet").classes("font-semibold")
        sug = suggest_input_state(m.file)
        with ui.row().classes("items-center gap-2"):
            ui.label("Input state").tooltip(tip("input_state"))
            ist = ui.toggle({"raw": "raw", "oim_cleaned": "OIM-cleaned"}, value=m.input_state) \
                .props("no-caps rounded unelevated toggle-color=primary").tooltip(tip("input_state")).mark("ms_input_state")
            hint = ui.label(f"suggested from the name: {'OIM-cleaned' if sug == 'oim_cleaned' else 'raw'} — "
                            "click to confirm").classes("text-xs text-amber-800")
            hint.set_visibility(m.input_state is None)
            ist.on_value_change(lambda e: save(input_state=e.value) and hint.set_visibility(False))
        _phases_section(m, save)
        with ui.row().classes("items-center gap-2"):
            ui.label("Indexing method").tooltip("How the map was indexed: Hough (the microscope's default) or "
                                                "spherical indexing (re-indexed in OIM Analysis). Shown as a tag in "
                                                "the file list, in reports, exports and the MCP map info.")
            ui.select(indexing.OPTIONS, value=m.indexing or "").props("dense options-dense").classes("w-64") \
                .mark("ms_indexing").on_value_change(lambda e: save(indexing=e.value or None, indexing_asked=True))
        _prias_section(m)
        # build direction in map coordinates (+ prefill from the sample's other maps)
        bd = m.build_direction
        ui.label("Build direction in map coordinates (x right, y down)").classes("text-sm mt-1").tooltip(tip("bd_map"))
        with ui.row().classes("items-end gap-2"):
            mode = ui.toggle({"angle": "angle", "vector": "vector"},
                             value="vector" if bd and bd.vector is not None else "angle").props("no-caps dense")
            ang = _num("angle from +x toward +y [°]", bd.angle_deg if bd else None, "bd_map", step=90).classes("w-56") \
                .mark("ms_bd_angle")
            vec = [_num(c, (bd.vector[i] if bd and bd.vector else None), "bd_map", step=1).classes("w-20")
                   for i, c in enumerate("xyz")]

        def save_bd(_=None):
            try:
                if mode.value == "angle":
                    nb = BuildDirection(angle_deg=float(ang.value)) if ang.value is not None else None
                else:
                    nb = (BuildDirection(vector=tuple(float(v.value or 0) for v in vec))
                          if any(v.value for v in vec) else None)
            except Exception as e:  # noqa: BLE001
                msg.text = "Not saved: " + str(e).splitlines()[-1]
                return
            save(build_direction=nb)
        for w in (ang, *vec):
            w.on("blur", save_bd)
            w.on("keydown.enter", save_bd)
        mode.on_value_change(save_bd)
        if bd is None:
            sib = [x for x in p.files() if x.sample_id == m.sample_id and x.id != m.id and x.build_direction]
            if sib:
                x = sib[0]
                txt = (f"{x.build_direction.angle_deg:g}°" if x.build_direction.angle_deg is not None
                       else str(x.build_direction.vector))

                def use(x=x):
                    if save(build_direction=x.build_direction):
                        if x.build_direction.angle_deg is not None:
                            mode.value, ang.value = "angle", x.build_direction.angle_deg
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"Prefill from {x.id} of this sample: {txt}").classes("text-xs ti-muted")
                    ui.button("Use", on_click=use).props("dense outline size=sm").mark("ms_bd_use")
            else:
                ui.label("Not needed to load the map; steps that use it (IPF along the build direction, prior-β length / "
                         "width along BD) ask for it. Never guessed.").classes("text-xs text-amber-800")
    else:
        ui.label("Image sheet").classes("font-semibold")
    # position + role
    pos = m.position
    ui.label("Position on the specimen (sample frame, mm)").classes("text-sm mt-1").tooltip(tip("position"))
    with ui.row().classes("items-end gap-2"):
        pw = {k: _num(k.replace("origin_", "").replace("_mm", " [mm]"), getattr(pos, k) if pos else None,
                      "position", step=0.1).classes("w-28") for k in ("origin_X_mm", "origin_Y_mm", "origin_Z_mm")}
        ax = ui.select(AXES, value=pos.map_x_axis if pos else "+X", label="map +x along").classes("w-32")
        ay = ui.select(AXES, value=pos.map_y_axis if pos else "-Y", label="map +y along").classes("w-32")

    def save_pos(_=None):
        vals = {k: w.value for k, w in pw.items()}
        if all(v is None for v in vals.values()):
            save(position=None)
            return
        try:
            save(position=MapPosition(**{k: float(v or 0) for k, v in vals.items()}, map_x_axis=ax.value,
                                      map_y_axis=ay.value))
        except Exception as e:  # noqa: BLE001
            msg.text = "Not saved: " + str(e).splitlines()[-1]
    for w in pw.values():
        w.on("blur", save_pos)
    ax.on_value_change(save_pos)
    ay.on_value_change(save_pos)
    stage = _stage(m)
    if stage:
        ui.label("Stage position from the JEOL .txt: " + ", ".join(f"{k} {v:g} mm" for k, v in stage.items())
                 + " (stage frame; map it to the specimen frame above).").classes("text-xs ti-muted")
    with ui.row().classes("items-end gap-2"):
        role = ui.select({None: "not set", **ROLES}, value=m.role, label="Role").classes("w-48") \
            .tooltip(tip("role")).mark("ms_role")
        role.on_value_change(lambda e: save(role=e.value))
        sug, why = suggest_role(m, s)
        if sug and sug != m.role:
            ui.button(f"Use suggestion: {sug}", on_click=lambda: setattr(role, "value", sug)) \
                .props("dense outline size=sm").tooltip(why).mark("ms_role_suggest")
        else:
            ui.label(why if not sug else f"suggested: {sug} ({why})").classes("text-xs ti-muted")


def _phases_section(m: MapInfo, save) -> None:
    """Phases of the .ang: found by crystal symmetry (6/mmm -> α, m-3m -> β), never by name; the user can change the
    mapping here (needed when the symmetry does not decide). Changing it marks the map's results stale."""
    from ..io.ang import read_header  # noqa: PLC0415
    from ..phases import ROLE_LABEL, resolve  # noqa: PLC0415

    try:
        hp = read_header(m.file).phases
    except OSError:
        return
    phases, problems = resolve(hp, m.phase_map)
    box = ui.column().classes("w-full gap-1 mt-1").mark("ms_phases")
    with box:
        with ui.row().classes("items-center gap-2"):
            ui.label("Phases").classes("text-sm").tooltip(tip("phases"))
            ui.label("found by crystal symmetry (6/mmm → α, m-3m → β), never by name").classes("text-xs ti-muted")
        if problems:
            with ui.column().classes("w-full gap-0 p-2 rounded bg-amber-50 border border-amber-300")                     .mark("ms_phases_ambiguous"):
                ui.label("The crystal symmetry does not tell the phases apart: choose the role of each phase in the "
                         "'role' lists below before loading.").classes("text-xs text-amber-900")
                for q in problems:
                    ui.label("• " + q).classes("text-xs text-amber-900")

        def changed(pid: int, role: str) -> None:
            cur = dict(m.phase_map or {})
            cur[str(pid)] = role
            sug = {str(ph.id): ph.suggested for ph in phases}
            new = {k: v for k, v in cur.items() if sug.get(k) != v}
            if save(phase_map=new or None):
                ui.notify("Phase mapping saved. Results of this map that used the old mapping are stale and "
                          "are computed again on the EBSD tab.", type="info")
        for ph in phases:
            with ui.row().classes("items-center no-wrap gap-2"):
                ui.label(f"{ph.id}").classes("text-sm w-4 ti-muted")
                ui.label(ph.original).classes("text-sm w-48 truncate").tooltip(f"name in the file: {ph.original}")
                ui.label(ph.laue or "unknown symmetry").classes("text-sm w-20")
                ui.select(ROLE_LABEL, value=ph.role, label="role",
                          on_change=lambda e, pid=ph.id: changed(pid, e.value))                     .props("dense options-dense").classes("w-48").mark(f"ms_phase_{ph.id}")
                ui.label("set here" if ph.source == "map sheet" else "by symmetry").classes("text-xs ti-muted")
        if m.phase_map:
            ui.button("Use the crystal symmetry again", icon="undo", on_click=lambda: save(phase_map=None))                 .props("dense flat no-caps size=sm").mark("ms_phases_reset")


def _prias_section(m: MapInfo) -> None:
    """PRIAS text exports found next to the .ang (<map>_prias.txt): channel, point check."""
    from ..io import prias  # noqa: PLC0415

    files = prias.find_files(m.file)
    with ui.column().classes("w-full gap-1 mt-1").mark("ms_prias"):
        with ui.row().classes("items-center gap-2"):
            ui.label("PRIAS (virtual BSE)").classes("text-sm").tooltip(tip("prias"))
            if not files:
                ui.label(f"no PRIAS export found next to the .ang (expected {Path(m.file).stem}_prias.txt in the "
                         "same folder); the IQ map is used as the registration reference").classes("text-xs ti-muted")
        for f in files:
            try:
                c = prias.check(f, m.file)
            except Exception as e:  # noqa: BLE001
                c = {"ok": False, "channel": "?", "message": f"cannot read it: {e}"}
            with ui.row().classes("items-center no-wrap gap-2"):
                ui.icon("check_circle" if c["ok"] else "error", size="18px")                     .classes("text-green-700" if c["ok"] else "text-red-700")
                ui.label(f"{c['channel']}").classes("text-sm w-36")
                ui.label(f.name).classes("text-xs ti-muted truncate").tooltip(str(f))
            ui.label(c["message"]).classes("text-xs " + ("ti-muted" if c["ok"] else "text-red-800"))                 .mark("ms_prias_msg")


def _inherited_fields(m: MapInfo, s: SampleInfo | None, save, msg) -> None:
    """Material / process / heat treatment / 4PB: greyed 'from sample' unless overridden for this file."""
    ui.label("From the sample sheet" + ("" if s else " (assign the file to a sample)")).classes("font-semibold mt-1")
    for k, lab in (("material", "Material"), ("process", "Process"), ("heat_treatment", "Heat treatment")):
        val, inh = effective(m, s, k)
        with ui.row().classes("items-center no-wrap gap-2"):
            w = ui.input(lab, value=val or "").classes("w-60" + (" ti-inherited" if inh else "")).tooltip(tip(k)) \
                .mark(f"ms_{k}")
            tag = ui.label("from sample" if inh else "this file only").classes("text-xs ti-muted w-24")

            def changed(_=None, k=k, w=w, tag=tag):
                inherited = effective(m, s, k)[0]
                if (w.value or "") == (inherited or "") and getattr(m, k) is None:
                    return
                if save(**{k: w.value or ""}):
                    w.classes(remove="ti-inherited")
                    tag.text = "this file only"
            w.on("blur", changed)
            if not inh:
                ui.button(icon="undo", on_click=lambda k=k, w=w, tag=tag: (
                    save(**{k: None}), w.set_value(getattr(s, k, "") if s else ""), w.classes(add="ti-inherited"),
                    tag.set_text("from sample"))).props("flat round dense size=sm").tooltip("Use the sample's value")
    b, inh = effective(m, s, "bending")
    txt = (f"outer {b.outer_span_mm:g} / inner {b.inner_span_mm:g} mm, {b.width_mm:g} × {b.thickness_mm:g} mm"
           + (f", load {b.load_N:g} N" if b.load_N is not None else "")) if b else "not entered"
    with ui.row().classes("items-center gap-2"):
        ui.label(f"4-point bending: {txt}").classes("text-sm" + (" ti-muted italic" if inh else ""))
        ui.label("from sample" if inh else "this file only").classes("text-xs ti-muted")
        if not inh:
            ui.button(icon="undo", on_click=lambda: save(bending=None)).props("flat round dense size=sm") \
                .tooltip("Use the sample's 4PB values")


def _stage(m: MapInfo) -> dict:
    if m.kind != "image":
        return {}
    try:
        from ..io import bse  # noqa: PLC0415

        txt = bse.find_sidecar(m.file)
        if not txt:
            return {}
        md = bse.read_metadata(txt)
        return {k: v for k, v in (("X", md.stage_x_mm), ("Y", md.stage_y_mm), ("Z", md.stage_z_mm),
                                  ("R", md.stage_rotation_deg), ("T", md.stage_tilt_deg)) if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def _metadata(p: Project, m: MapInfo) -> None:
    ui.label("File metadata").classes("font-semibold")
    f = Path(m.file)
    rows: list[tuple[str, str]] = []
    if f.is_file():
        st = f.stat()
        rows.append(("size", f"{st.st_size / 1e6:.1f} MB"))
    try:
        if m.kind == "ang" and f.is_file():
            from ..io.ang import read_header  # noqa: PLC0415

            h = read_header(f)
            rows += [("grid", "hexagonal" if h.is_hex else "square"), ("step", f"{h.step_um:g} µm"),
                     ("points (header)", f"{h.n_points:,}"),
                     ("phases", ", ".join(f"{ph.get('id')} {ph.get('MaterialName', '?')}" for ph in h.phases) or "–"),
                     ("scan ID", h.fields.get("SCANID", "–")),
                     ("PRIAS columns declared", "yes" if h.declares_prias else "no")]
        elif m.kind == "image" and f.is_file():
            from ..io import bse  # noqa: PLC0415

            txt = bse.find_sidecar(f)
            if txt:
                md = bse.read_metadata(txt)
                rows += [("JEOL sidecar", txt.name), ("instrument", md.instrument), ("detector", md.signal),
                         ("magnification", f"{md.magnification:g}×" if md.magnification else "–"),
                         ("accelerating voltage", f"{md.accelerating_voltage_kV:g} kV" if md.accelerating_voltage_kV else "–"),
                         ("working distance", f"{md.working_distance_mm:g} mm" if md.working_distance_mm else "–"),
                         ("pixel size", f"{md.pixel_size_um * 1000:.2f} nm" if md.pixel_size_um else "–"),
                         ("image size", f"{md.image_size_px[0]} × {md.image_size_px[1]} px" if md.image_size_px else "–"),
                         ("date", md.date or "–")]
            else:
                rows.append(("JEOL sidecar", "none found (.txt with the same name)"))
        elif m.kind == "up2" and f.is_file():
            from ..io.up2 import read_header  # noqa: PLC0415

            from ..modules.up2_pair import paired  # noqa: PLC0415

            h = read_header(f)
            pr = paired(p, f)
            rows += [("scan points", f"{h.n_points:,}"),
                     ("patterns in the file", f"{h.n_patterns:,} of {h.pattern_width} × {h.pattern_height} px"
                      + (f" ({h.n_padding:,} padding patterns, one per short hex row, skipped)" if h.n_padding else "")),
                     ("grid", f"{'hexagonal' if h.hex_grid else 'square'}, {h.n_cols} × {h.n_rows}"),
                     ("step", f"{h.x_step_um:g} × {h.y_step_um:g} µm"),
                     ("scan ID", h.scan_id or "–"),
                     ("paired map", (f"{pr.ang.name}" + (f" (map {pr.map_id})" if pr.map_id else " (not linked)"))
                      if pr else "no .ang with the same scan ID"),
                     ("file size check", "ok" if h.size_ok else "size does not match the header"),
                     ("note", "streamed in chunks for indexing; never loaded fully")]
    except Exception as e:  # noqa: BLE001
        rows.append(("could not read", str(e)[:200]))
    with ui.grid(columns=2).classes("gap-x-4 gap-y-0 text-sm").mark("file_meta"):
        for k, v in rows:
            ui.label(k).classes("ti-muted")
            ui.label(str(v) if v not in (None, "") else "–")
