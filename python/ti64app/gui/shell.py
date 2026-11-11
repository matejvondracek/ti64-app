"""Top bar (home: New / Open Project; project: tabs Files | View | EBSD | Crack | Stitch, project name + menu centred,
save indicator and Settings gear on the right), the Settings dialog, project dialogs (new, open, save copy) and
plain-language error notifications with "Open log"."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from nicegui import ui

from .. import service
from ..backends import backend_status, get_backend
from ..project import Project, list_projects, projects_dir, recent_projects
from ..settings import (FigureStyle, IpfKeySetting, Paths, find_settings_file, load_settings, logs_dir,
                        preferences_path, save_preferences)
from . import dialogs
from .state import SAVE, STATE, autosave, current_project, log, open_project, plain_error
from .theme import apply_theme
from .tips import tip

TABS = [("Files", "/files", "folder"), ("View", "/view", "image_search"), ("EBSD", "/ebsd", "grain"),
        ("Crack", "/crack", "timeline"), ("Stitch", "/stitch", "view_module")]
DESKTOP: dict = {"request_open": None, "spawn": None, "set_title": None}    # filled by desktop.py


def error(e: BaseException, what: str = "") -> None:
    """Plain-language notification + full traceback in the log file."""
    from ..sample import SheetIncomplete  # noqa: PLC0415

    if isinstance(e, SheetIncomplete):                  # not an error: a value the user still has to enter
        sheet_missing(e.map_id, plain_error(e))
        return
    log.error("%s%s", f"{what}: " if what else "", e, exc_info=e)
    msg = (f"{what}: " if what else "") + plain_error(e)
    notify_actions(msg, [("Open log", "() => fetch('/api/ti64/open_log', {method: 'POST'})")], type="negative",
                   timeout=12, close_button="OK")


def notify_actions(msg: str, actions: list[tuple[str, str]], **kw) -> None:
    """Notification with action buttons (label, JS handler). ui.notification, not ui.notify: only the element turns
    ':handler' strings into functions, with ui.notify the buttons did nothing."""
    kw.setdefault("multi_line", True)
    ui.notification(msg, actions=[{"label": lab, "color": "white", ":handler": js} for lab, js in actions], **kw)


_LAST_SHEET_NOTE: dict[str, float] = {}


def sheet_missing(map_id: str, msg: str) -> None:
    """Something on the map sheet is missing for a step: say what and link straight to the sheet (Files tab with
    the file selected). The same message is not repeated within 30 s (redraws of a map would repeat it)."""
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    now = time.monotonic()
    if now - _LAST_SHEET_NOTE.get(msg, -1e9) < 30:
        return
    _LAST_SHEET_NOTE[msg] = now
    notify_actions(msg, [("Open map sheet",
                          f"() => window.location.href = '/files?sel=' + encodeURIComponent({json.dumps(map_id)})")],
                   type="warning", timeout=10, close_button="Later", position="bottom-right")


def bytes_h(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---- top bar ---------------------------------------------------------------------------------------------
def header(active: str | None = None) -> Project | None:
    """Draw the theme + top bar; returns the open project (None on the home page)."""
    apply_theme()
    p = current_project()
    with ui.header().classes("ti-topbar items-center px-3 py-1 gap-2 no-wrap").mark("topbar"):
        if p is None:
            ui.button("New Project", icon="create_new_folder", on_click=new_project_dialog) \
                .props("color=white text-color=primary").mark("home_new")
            with ui.button("Open Project", icon="folder_open").props("color=white text-color=primary") \
                    .mark("home_open"):
                with ui.menu().props("auto-close"):
                    ui.menu_item("Browse for a project folder…", on_click=browse_open).mark("open_browse")
                    rec = recent_projects()
                    if rec:
                        ui.separator()
                        ui.label("Recent projects").classes("px-4 pt-2 text-xs ti-muted")
                    for r in rec:
                        ui.menu_item(f"{Path(r).name}   —   {r}", on_click=lambda r=r: request_open(r)) \
                            .mark(f"recent_{Path(r).name}")
                    others = [n for n in list_projects(projects_dir())
                              if str(projects_dir() / n) not in rec]
                    if others:
                        ui.separator()
                        ui.label(f"In {projects_dir()}").classes("px-4 pt-2 text-xs ti-muted")
                    for n in others[:15]:
                        ui.menu_item(n, on_click=lambda n=n: request_open(str(projects_dir() / n))) \
                            .mark(f"recent_{n}")
            ui.space()
            ui.label("Ti64 EBSD/BSE").classes("text-white text-lg font-semibold mr-2")
            right_tools()
            return None
        with ui.row().classes("gap-1 no-wrap items-center").style("flex: 1 1 0; min-width: 0"):
            for label, path, icon in TABS:
                b = ui.button(label, icon=icon, on_click=lambda path=path: ui.navigate.to(path)) \
                    .props("unelevated dense color=white text-color=secondary" if active == label
                           else "flat dense color=white") \
                    .classes("ti-tab" + (" active" if active == label else "")) \
                    .mark(f"tab_{label.lower()}")
                b.props(f'aria-current={"page" if active == label else "false"}')
        name = p.info().name
        with ui.button(name).props("flat no-caps color=white icon-right=expand_more") \
                .classes("text-lg font-semibold italic").style("flex: 0 1 auto; max-width: 34vw") \
                .tooltip(str(p.root)).mark("project_menu"):
            with ui.menu().props("auto-close"):
                ui.menu_item("New project…", on_click=new_project_dialog)
                ui.menu_item("Open project…", on_click=browse_open)
                for r in [r for r in recent_projects() if Path(r) != p.root][:6]:
                    ui.menu_item(f"Open {Path(r).name}", on_click=lambda r=r: request_open(r))
                ui.separator()
                ui.menu_item("Save copy as…", on_click=lambda: save_copy_dialog(p)).mark("save_copy")
                ui.menu_item("Show project folder", on_click=lambda: _startfile(p.root))
                ui.separator()
                ui.menu_item("Close project", on_click=close_project).mark("close_project")
        with ui.row().classes("gap-2 no-wrap items-center justify-end").style("flex: 1 1 0; min-width: 0"):
            st = ui.label(SAVE.text).classes("text-sm text-white/90 truncate").mark("save_status")

            def upd_status():
                if st.text != SAVE.text:
                    st.text = SAVE.text
                    st.classes(replace="text-sm truncate " + ("text-amber-300" if SAVE.error else "text-white/90"))
            ui.timer(0.3, upd_status)
            right_tools()
    return p


def right_tools() -> None:
    """Memory use (fix 3.1), Feedback (fix 3.2) and the Settings gear, right end of the top bar."""
    mem_lab = ui.label("").classes("text-xs text-white/90 whitespace-nowrap px-2 py-1 rounded cursor-help") \
        .mark("mem_use")
    with mem_lab:
        mem_tip = ui.tooltip("").classes("text-sm").style("white-space: pre-line")

    def upd_mem():
        from ..sysinfo import memory_status, memory_text  # noqa: PLC0415

        try:
            m = memory_status()
        except Exception:  # noqa: BLE001
            return
        short, tip_ = memory_text(m)
        mem_lab.text = short
        mem_tip.text = tip_
        mem_lab.classes(replace="text-xs whitespace-nowrap px-2 py-1 rounded cursor-help " +
                        ("bg-orange-500 text-white font-semibold" if m["warn"] else "text-white/90"))
    upd_mem()
    ui.timer(3.0, upd_mem)
    ui.button("Feedback", icon="feedback", on_click=feedback_dialog).props("flat dense no-caps color=white") \
        .mark("feedback_btn").tooltip("Report a bug, an idea or a question (GitHub issue or e-mail)")
    ui.button(icon="settings", on_click=lambda: settings_dialog(current_project())) \
        .props("flat round color=white").tooltip("Settings").mark("settings_gear") \
        .props('aria-label="Settings"')


# ---- feedback (fix 3.2) ------------------------------------------------------------------------------------
async def feedback_dialog() -> None:
    """Description + category; app version, backend, OS and the last 100 log lines are added (paths shortened to
    file names, issues are public). The window screenshot is taken now (before the dialog covers it) but only
    included when ticked."""
    import webbrowser  # noqa: PLC0415

    from nicegui import run  # noqa: PLC0415

    from .. import __version__  # noqa: PLC0415
    from .. import feedback as fb  # noqa: PLC0415
    from ..sysinfo import system_summary  # noqa: PLC0415

    shot = await run.io_bound(fb.window_screenshot)
    s = load_settings()
    p = current_project()
    backend = p.info().backend if p else s.backend
    lines = fb.log_tail()
    sysi = system_summary()
    with ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-2").mark("feedback_dialog"):
        with ui.row().classes("w-full items-center"):
            ui.label("Feedback").classes("text-lg font-semibold")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense").props('aria-label="Close"')
        cat = ui.toggle({"bug": "Bug", "idea": "Idea", "question": "Question"}, value="bug") \
            .props("no-caps rounded unelevated toggle-color=primary").mark("fb_category")
        desc = ui.textarea("What happened / what would help? (steps to reproduce for a bug)") \
            .classes("w-full").props("autogrow outlined").mark("fb_text")
        ui.label(f"Added automatically: app {__version__}, backend {backend}, {sysi['os']}, Python {sysi['python']}, "
                 f"{sysi['ram']} RAM and the last {len(lines)} log lines with every path shortened to its file "
                 "name.").classes("text-xs ti-muted").mark("fb_info")
        with ui.expansion("Show the log lines that are added").classes("w-full text-sm").mark("fb_log"):
            ui.code("\n".join(lines) or "(no log yet)").classes("w-full text-xs") \
                .style("max-height: 14rem; overflow: auto")
        with ui.row().classes("items-center gap-1"):
            shot_cb = ui.checkbox("Add a screenshot of this window", value=False).mark("fb_screenshot") \
                .tooltip("Off by default: GitHub issues are public. Shows the window as it was when you pressed "
                         "Feedback (this dialog is not in it).")
            if shot is None:
                shot_cb.disable()
                ui.label("(only in the desktop app window)").classes("text-xs ti-muted")
        ui.label(f"GitHub: {s.feedback.repo_url} · e-mail: {s.feedback.email} (both editable in Settings, section "
                 "'Feedback')").classes("text-xs ti-muted break-all")

        def report() -> dict:
            return fb.build_report(desc.value or "", cat.value, backend, lines, sysi)

        def described() -> bool:
            if (desc.value or "").strip():
                return True
            ui.notify("Describe the problem or idea first (the text box in this dialog).", type="warning")
            return False

        def github():
            if not described():
                return
            r = report()
            webbrowser.open(fb.github_issue_url(s.feedback.repo_url, r))
            if shot_cb.value and shot:
                z = fb.write_zip(logs_dir().parent / "feedback", r, shot)
                _reveal(z)
                ui.notify(f"GitHub opens in your browser. Drag {z.name} (selected in the Explorer window) into the "
                          "issue to add the screenshot.", type="info", multi_line=True, timeout=12000)
            else:
                ui.notify("GitHub opens in your browser with the title and text filled in; check it and press "
                          "'Submit new issue' there.", type="info", multi_line=True)
            dlg.close()

        def email():
            if not described():
                return
            r = report()
            z = fb.write_zip(logs_dir().parent / "feedback", r, shot if shot_cb.value else None)
            webbrowser.open(fb.mailto_url(s.feedback.email, cat.value, r, z.name))
            _reveal(z)
            ui.notify(f"Your e-mail program opens with the subject '{fb.subject(cat.value)}'. Attach {z.name} "
                      "(selected in the Explorer window that just opened).", type="info", multi_line=True,
                      timeout=12000)
            dlg.close()
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Send e-mail", icon="mail", on_click=email).props("outline").mark("fb_email")
            ui.button("Open GitHub issue", icon="open_in_new", on_click=github).mark("fb_github")
    dlg.open()


def _reveal(path: Path) -> None:
    """Explorer window with the file selected."""
    import subprocess  # noqa: PLC0415

    try:
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
    except Exception as e:  # noqa: BLE001
        log.warning("cannot open Explorer: %s", e)


def _startfile(path: Path) -> None:
    try:
        os.startfile(str(path))          # noqa: S606  (Windows Explorer)
    except Exception as e:  # noqa: BLE001
        error(e)


# ---- open / new / copy / close ---------------------------------------------------------------------------
def request_open(root: str | Path) -> None:
    """Open a project: brings its window to the front if it is open in another window, opens a new window if this
    one already shows another project (desktop app), else opens it here."""
    root = Path(root)
    if not (root / "project.json").is_file():
        ui.notify(f"{root} is not a Ti64 project folder (no project.json).", type="warning")
        return
    try:
        handled = DESKTOP["request_open"](root) if DESKTOP["request_open"] else "here"
        if handled == "focused":
            ui.notify(f"{root.name} is already open in another window; brought it to the front.")
            return
        if handled == "spawned":
            ui.notify(f"Opening {root.name} in a new window…")
            return
        p = open_project(root)
        if DESKTOP["set_title"]:
            DESKTOP["set_title"](p)
        tab = p.gui_state().get("tab") or "/files"
        ui.navigate.to(tab)
    except Exception as e:  # noqa: BLE001
        error(e, "Could not open the project")


async def browse_open() -> None:
    d = await dialogs.pick_folder("Open a Ti64 project folder", str(projects_dir()))
    if d:
        request_open(d)


def close_project() -> None:
    if DESKTOP.get("release"):
        DESKTOP["release"]()
    STATE["project"] = None
    ui.navigate.to("/")


def new_project_dialog() -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full gap-3"):
        ui.label("New project").classes("text-lg font-semibold")
        ui.label("A project folder holds the recipe, sample sheets and results. Your data files stay where they "
                 "are: they are linked, never copied.").classes("text-sm ti-muted")
        name = ui.input("Project name").classes("w-full").props("autofocus").mark("np_name")
        with ui.row().classes("w-full items-end no-wrap gap-2"):
            where = ui.input("Location", value=str(projects_dir())).classes("grow").mark("np_where")

            async def browse():
                d = await dialogs.pick_folder("Where to create the project", where.value)
                if d:
                    where.value = d
            ui.button("Browse…", icon="folder", on_click=browse).props("outline").mark("np_browse")
        hint = ui.label().classes("text-xs ti-muted")
        for w in (name, where):
            w.on_value_change(lambda: hint.set_text(f"Creates {Path(where.value) / (name.value or '…')}"))

        def create():
            n = (name.value or "").strip()
            if not n or any(c in n for c in '<>:"/\\|?*'):
                ui.notify("Enter a project name (no < > : \" / \\ | ? *).", type="warning")
                return
            try:
                p = autosave(Project.create, n, base=Path(where.value), backend=load_settings().backend)
            except Exception as e:  # noqa: BLE001
                error(e, "Could not create the project")
                return
            dlg.close()
            request_open(p.root)
        name.on("keydown.enter", create)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Create project", icon="check", on_click=create).mark("np_create")
    dlg.open()


def save_copy_dialog(p: Project) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full gap-3"):
        ui.label("Save copy as…").classes("text-lg font-semibold")
        ui.label("Duplicates the project (linked files, recipe, sample sheets, results) to try other settings. "
                 "The cache and the undo history are not copied.").classes("text-sm ti-muted")
        name = ui.input("Name of the copy", value=f"{p.info().name}_copy").classes("w-full").mark("copy_name")
        where = ui.input("Location", value=str(p.root.parent)).classes("w-full")

        async def browse():
            d = await dialogs.pick_folder("Where to put the copy", where.value)
            if d:
                where.value = d

        def go():
            try:
                q = autosave(p.save_copy, Path(where.value) / name.value.strip(), name.value.strip())
                dlg.close()
                ui.notify(f"Copy saved: {q.root}", type="positive")
                request_open(q.root)
            except Exception as e:  # noqa: BLE001
                error(e, "Could not save the copy")
        with ui.row().classes("w-full justify-end"):
            ui.button("Browse…", icon="folder", on_click=browse).props("outline")
            ui.space()
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Save copy", icon="content_copy", on_click=go).mark("copy_go")
    dlg.open()


def check_interrupted(p: Project) -> None:
    """If the app closed while a step was running, say so and offer to rerun it."""
    it = p.interrupted()
    if not it:
        return
    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-[30rem] max-w-full"):
        ui.label("A step was interrupted").classes("text-lg font-semibold")
        ui.label(f"The app closed while '{it['op']}' {it.get('params', {}).get('map_id', '')} was running "
                 f"(started {it.get('started', '?')}). Its results were not saved.").classes("text-sm")

        async def rerun():
            dlg.close()
            from nicegui import run  # noqa: PLC0415

            n = ui.notification(f"Re-running {it['op']}…", spinner=True, timeout=None)
            try:
                p.clear_interrupted()
                await run.io_bound(service.run, p, it["op"], it["params"], it.get("backend"))
                ui.notify(f"{it['op']} finished", type="positive")
                ui.navigate.reload()
            except Exception as e:  # noqa: BLE001
                error(e, f"{it['op']} failed")
            finally:
                n.dismiss()

        def dismiss():
            p.clear_interrupted()
            dlg.close()
        with ui.row().classes("w-full justify-end"):
            ui.button("Dismiss", on_click=dismiss).props("flat").mark("interrupted_dismiss")
            ui.button("Rerun step", icon="replay", on_click=rerun).mark("interrupted_rerun")
    dlg.open()


# ---- Settings dialog --------------------------------------------------------------------------------------
CMAPS = ["parula", "viridis", "turbo", "jet", "gray", "hsv", "hot", "cool"]
IPF_KEYS = ["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"]
DIRECTIONS = ["X", "Y", "Z", "build", "custom"]


def settings_dialog(p: Project | None) -> None:
    """Every change is saved immediately (preferences file; backend switch per project, undoable)."""
    s = load_settings()
    status = backend_status()
    with ui.dialog() as dlg, ui.card().classes("w-[46rem] max-w-full max-h-[90vh] gap-2").mark("settings_dialog"):
        with ui.row().classes("w-full items-center"):
            ui.label("Settings").classes("text-xl font-semibold")
            ui.space()
            ui.label("Changes are saved immediately.").classes("text-xs ti-muted")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense").props('aria-label="Close"')
        with ui.scroll_area().classes("w-full").style("height: min(70vh, 640px)"):
            with ui.column().classes("w-full gap-3 pr-3"):
                _settings_body(s, status, p)
    dlg.open()


def _settings_body(s, status, p: Project | None) -> None:
    def save(msg: str = "") -> None:
        try:
            autosave(save_preferences, s)
            if msg:
                ui.notify(msg, type="positive")
        except Exception as e:  # noqa: BLE001
            error(e, "Settings not saved")

    with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_backend"):
        ui.label("Analysis backend").classes("font-semibold")
        avail = {n: n for n, r in status.items() if not r}
        cur = (p.info().backend if p else s.backend)
        radio = ui.radio(avail, value=cur if cur in avail else next(iter(avail), None)).props("inline") \
            .mark("backend")
        for n, r in status.items():
            if r:
                ui.label(f"{n}: unavailable — {r}").classes("text-sm text-amber-800")
            else:
                running = n == "matlab" and getattr(get_backend(n), "running", False)
                ui.label(f"{n}: available" + (" (engine running)" if running else "")).classes("text-sm text-green-800")
        if p:
            ui.label(f"Project {p.info().name} uses {p.info().backend}. Changing it marks results of the other "
                     "backend stale (undoable).").classes("text-xs ti-muted")

        def on_backend(e):
            s.backend = e.value
            save()
            if p:
                try:
                    n = autosave(service.switch_backend, p, e.value)
                    ui.notify(f"Backend {e.value}" + (f"; {n} steps marked stale" if n else ""), type="positive")
                except Exception as ex:  # noqa: BLE001
                    error(ex)
        radio.on_value_change(on_backend)

    with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_ipf"):
        ui.label("IPF colour key per phase (shared by both renderers)").classes("font-semibold")
        for phase in list(s.figure.ipf):
            k = s.figure.ipf[phase]
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(phase).classes("w-24")
                kk = ui.select(IPF_KEYS, value=k.key, label="key").classes("w-36").tooltip(tip("ipf_key"))
                dd = ui.select(DIRECTIONS, value=k.direction, label="reference direction").classes("w-40") \
                    .tooltip(tip("ipf_dir"))
                cv = [ui.number(c, value=v, step=0.1).classes("w-20")
                      for c, v in zip("xyz", k.custom_vector or (0.0, 0.0, 1.0))]
                kif = ui.checkbox("key in figure", value=k.key_in_figure)

                def upd(_=None, phase=phase, kk=kk, dd=dd, cv=cv, kif=kif):
                    s.figure.ipf[phase] = IpfKeySetting(key=kk.value, direction=dd.value, key_in_figure=kif.value,
                                                        custom_vector=tuple(float(c.value or 0) for c in cv)
                                                        if dd.value == "custom" else None)
                    save()
                for w in (kk, dd, kif, *cv):
                    w.on_value_change(upd)
        ui.label("build = the build direction of the map sheet; custom = vector in the map frame (x right, y down).") \
            .classes("text-xs ti-muted")

    with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_style"):
        ui.label("Figure style").classes("font-semibold")
        f = s.figure
        with ui.row().classes("items-end gap-2"):
            cms = ui.select(CMAPS, value=f.colormap_scalar, label="colour map CI / IQ / fit").classes("w-48")
            cmk = ui.select(CMAPS, value=f.colormap_kam, label="colour map KAM").classes("w-40")
            lw = ui.number("boundary line width [pt]", value=f.boundary_line_width, step=0.25, min=0.1).classes("w-44")
            bc = ui.input("boundary colour", value=f.boundary_color).classes("w-32")
        with ui.row().classes("items-end gap-2"):
            sb = ui.checkbox("scale bar", value=f.scale_bar)
            width = ui.number("figure width [cm]", value=f.width_cm, step=0.5, min=1).classes("w-40")
            dpi = ui.number("resolution [DPI]", value=f.dpi, step=50, min=72).classes("w-36")

        def upd_style(_=None):
            try:
                s.figure = FigureStyle(ipf=s.figure.ipf, colormap_scalar=cms.value, colormap_kam=cmk.value,
                                       boundary_line_width=float(lw.value or 1), boundary_color=bc.value or "k",
                                       scale_bar=sb.value, width_cm=float(width.value or 12),
                                       dpi=int(dpi.value or 600))
            except Exception as e:  # noqa: BLE001
                error(e)
                return
            save()
        for w in (cms, cmk, lw, bc, sb, width, dpi):
            w.on_value_change(upd_style)
        ui.label("parula is a MATLAB colour map; pymtex draws viridis instead.").classes("text-xs ti-muted")

    with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_paths"):
        ui.label("Paths (this machine)").classes("font-semibold")
        tf = find_settings_file()
        ui.label(f"TOML: {tf or 'none found'}; preferences: {preferences_path()}; logs: {logs_dir()}") \
            .classes("text-xs ti-muted break-all")
        toml_paths = Paths(**(tomllib.loads(tf.read_text(encoding="utf-8")).get("paths", {}) if tf else {}))
        pw = {}
        for k in Paths.model_fields:
            with ui.row().classes("w-full no-wrap items-end gap-1"):
                pw[k] = ui.input(k, value=getattr(s.paths, k)).classes("grow").mark(k)

                async def browse(k=k):
                    d = await dialogs.pick_folder(f"Choose {k}", pw[k].value)
                    if d:
                        pw[k].value = d
                ui.button(icon="folder", on_click=browse).props("flat round dense").tooltip("Browse…")

        def upd_paths(_=None):
            changed = {k: w.value for k, w in pw.items() if (w.value or "") != getattr(toml_paths, k)}
            try:
                autosave(save_preferences, s, paths=changed)
            except Exception as e:  # noqa: BLE001
                error(e)
        for w in pw.values():
            w.on("blur", upd_paths)
            w.on("keydown.enter", upd_paths)

    with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_feedback"):
        ui.label("Feedback").classes("font-semibold")
        repo = ui.input("GitHub repository (issues)", value=s.feedback.repo_url).classes("w-full").mark("fb_repo")
        mail = ui.input("E-mail address for feedback", value=s.feedback.email).classes("w-full").mark("fb_mail")

        def upd_fb(_=None):
            s.feedback.repo_url = (repo.value or "").strip() or "https://github.com/matejvondracek/ti64-app"
            s.feedback.email = (mail.value or "").strip() or "vondrm34@cvut.cz"
            save()
        for w in (repo, mail):
            w.on("blur", upd_fb)
            w.on("keydown.enter", upd_fb)
        ui.label("Used by the Feedback button in the top bar.").classes("text-xs ti-muted")

    if p:
        with ui.column().classes("ti-card w-full p-3 gap-1").mark("settings_disk"):
            ui.label(f"Project disk use — {p.root}").classes("font-semibold")
            lab = ui.label().classes("text-sm").mark("disk_use")

            def show_use():
                u = p.disk_usage()
                lab.text = (f"total {bytes_h(u['total'])}: results {bytes_h(u['outputs'])}, cache {bytes_h(u['cache'])}, "
                            f"undo history {bytes_h(u['history'])}, maps / figures {bytes_h(u['maps'])}")
            show_use()

            def clear():
                n = p.clear_cache()
                show_use()
                ui.notify(f"Cache cleared ({bytes_h(n)} freed). Cached results are rebuilt from the recipe when "
                          "needed.", type="positive")
            ui.button("Clear cache", icon="delete_sweep", on_click=clear).props("outline").mark("clear_cache")
            ui.label("The cache holds coloured map tiles, viewer layers and scratch copies; results and the recipe "
                     "are kept.").classes("text-xs ti-muted")
