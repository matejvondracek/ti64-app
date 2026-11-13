"""EBSD tab (docs/gui_mockups/02_ebsd.jpg). Left: scrollable accordion of edit sections (Load + cleanup, Maps and IPF
key, Grains and boundaries, KAM / GND, Prior-beta reconstruction + sweep, Grain size, Annotations, OIM comparison,
any operation), then History and Compare backends; Export and Compare to original at the bottom. Right: "Sample /
file" with a dropdown, the tiled map viewer (zoom / pan, overlays, annotations) and a Results tab for figures and
tables.

Numbers are input boxes with increment arrows (no sliders), units in the labels, every option has a hover
explanation (tips.py). Apply runs the step with a progress bar and adds one undoable recipe step. Values typed but
not applied are kept as drafts in gui_state.json and restored on reopen ("not applied")."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from nicegui import run as ng_run
from nicegui import ui

from .. import layers as lay
from .. import progress, service
from ..annotations import ROI_TYPES, Annotation, load_annotations, roi_box
from ..backends import backend_status
from ..backends.base import CLEAN_LABELS
from ..texture import PF_ALPHA, PF_BETA
from ..project import Project
from ..settings import IpfKeySetting, load_settings, save_preferences
from . import dialogs, tiles
from .export_dialog import export_dialog
from ..legends import default_item
from .history_panel import compare_section, history_section, ops_section
from .shell import check_interrupted, error, header, sheet_missing
from .state import GuiState, stable
from .tips import tip
from .viewer import TOOLS, MapViewer, shapes_of

IPF_KEYS = ["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"]
DIRECTIONS = ["X", "Y", "Z", "build", "custom"]
RECON_FIG_KINDS = ["parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                   "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist"]
DEFAULT_OV_COLOR = {"hagb": "#000000", "lagb": "#a6a6a6", "interface": "#d91a1a", "beta_gb": "#000000",
                    "crack": "#e00000"}
ON_DEMAND = ("kam", "grod", "gos", "gnd")             # listed in the map selector, computed on first use (fix 3.9)
IPF_KINDS = ("ipf", "parent_ipf")
PANEL_TABS = ("results", "pf", "ipf", "elastic")
REGION_LABELS = {"map": "whole map", "roi": "ROI", "sample": "all maps of the sample"}
IPF_DIR_OPTIONS = ["BD", "beam axis", "width", "thickness", "map x", "map y", "map z", "custom"]      # map tabs without the map viewer (phase 6.7: texture, elastic)
# cleanup sub-panels (fix 5): operation, icon, fields (name, num | check, label, default, number options)
CLEAN_UI = [
    ("ci_threshold", "filter_alt", [("ci_threshold", "num", "CI threshold (points below → not indexed)", 0.1,
                                     {"step": 0.05, "min": 0, "max": 1})]),
    ("spike_removal", "flash_off", [("spike_angle_deg", "num", "spike angle [°]", 5.0, {"step": 0.5, "min": 0})]),
    ("noise_removal", "grain", [("noise_angle_deg", "num", "grain angle [°]", 3.0, {"step": 0.5, "min": 0}),
                                ("noise_max_indexed_px", "num", "max noise grain [px]", 2, {"step": 1, "min": 0}),
                                ("fill", "check", "fill removed points from neighbours", True, {})]),
    ("void_fill", "format_color_fill", [("max_void_px", "num", "max void size [px]", 4, {"step": 1, "min": 0}),
                                        ("angle_deg", "num", "grain angle of the neighbour vote [°]", 3.0,
                                         {"step": 0.5, "min": 0})]),
    ("grain_dilation", "open_in_full", [("tolerance_deg", "num", "grain tolerance angle [°]", 5.0,
                                         {"step": 0.5, "min": 0}),
                                        ("min_grain_px", "num", "minimum grain size [px]", 2, {"step": 1, "min": 1}),
                                        ("multi_row", "check", "grain must span multiple rows", False, {}),
                                        ("single_iteration", "check", "single iteration", False, {})]),
    ("smoothing", "blur_linear", [("hq_alpha", "num", "filter strength", 0.5, {"step": 0.1, "min": 0}),
                                  ("hq_threshold_deg", "num", "filter limit [°]", 15.0, {"step": 1, "min": 0})]),
]
INT_FIELDS = {"noise_max_indexed_px", "max_void_px", "min_grain_px"}
# rough share of the whole load (click -> first image) per stage, for the progress bar when a stage gives no fraction
STAGE_FRAC = {"starting": 0.02, "reading file": 0.1, "transferring data": 0.4, "cleanup from cache": 0.45,
              "building grid": 0.6, "colouring map": 0.75, "drawing": 0.92}
ANCHOR_LABELS = {"": "free (where dragged)", "tl": "top left", "tc": "top centre", "tr": "top right",
                 "bl": "bottom left", "bc": "bottom centre", "br": "bottom right"}


def key_url(pngs) -> str:
    """The IPF key PNG(s) of a layer as one data URL (side by side), for the legend on the map."""
    import base64  # noqa: PLC0415
    import io  # noqa: PLC0415

    from ..legends import key_image  # noqa: PLC0415

    im = key_image(420, pngs)
    if im is None:
        return ""
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


GS_FIG_KINDS = ["alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd", "colony_ecd_area",
                "beta_ecd", "beta_width", "beta_length", "colony_map"]
GS_FIG_LABELS = {"alpha_ecd": "α grain ECD", "alpha_ecd_area": "α grain ECD, area-weighted",
                 "lath_thickness": "lath thickness (intercept)", "lath_minor_axis": "lath minor axis",
                 "colony_ecd": "colony ECD", "colony_ecd_area": "colony ECD, area-weighted",
                 "colony_map": "colony map", "beta_ecd": "prior-β ECD", "beta_width": "prior-β width (across BD)",
                 "beta_length": "prior-β length (along BD)"}
GS_BETA_KINDS = ("beta_ecd", "beta_width", "beta_length")


def map_options(p: Project, kinds=("ang",)) -> dict[str, str]:
    """Files for the 'Sample / file' dropdown, grouped (sorted) by sample."""
    files = [m for m in p.files() if m.kind in kinds]
    files.sort(key=lambda m: (m.sample_id is None, m.sample_id or "", Path(m.file).name.lower()))
    return {m.id: f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}" for m in files}


BADGE_TEXT = {"changed": "● changed, not applied", "stale": "● stale", "applied": "✓ applied"}
BADGE_CLASS = {"changed": "ti-draft", "stale": "ti-stale", "applied": "ti-applied"}


class Section:
    """One accordion section: registers its inputs, keeps unapplied values as drafts (gui_state.json) and shows a
    status badge (fix 5, ti64app.badges): none while the section was never used for this map; 'changed, not applied'
    when the fields differ from the last applied step of the section; 'applied <time>' when that step is current;
    'stale' when an upstream step changed after it. track() tells the section which step is its last applied one."""

    def __init__(self, gs: GuiState, key: str, title: str, icon: str, opened: bool = False,
                 group: str | None = "ebsd", sub: bool = False) -> None:
        self.gs, self.key, self.title = gs, key, title
        self.fields: dict[str, ui.element] = {}
        self.defaults: dict = {}
        self.project = None
        self.step_fn = None                          # () -> last applied step of this section (or None)
        self.recorded_fn = None                      # step -> {field name: recorded value} to compare with
        self.children: list[Section] = []
        self.exp = ui.expansion(group=group, value=opened).classes("w-full " + ("ti-subsection" if sub else
                                                                                "ti-section")).mark(f"sec_{key}")
        with self.exp.add_slot("header"):
            with ui.row().classes("w-full items-center no-wrap gap-3"):
                ui.icon(icon, size="18px" if sub else "22px").classes("text-primary")
                self.title_label = ui.label(title).classes(("text-sm " if sub else "") + "font-semibold grow")
                self.chip = ui.label("").classes("ti-draft").mark(f"badge_{key}")
                with self.chip:
                    self.chip_tip = ui.tooltip("")
        self.chip.set_visibility(False)
        self.drafts = dict(gs.get("drafts", key, default={}) or {})

    def __enter__(self):
        self.exp.__enter__()
        return self

    def __exit__(self, *a):
        self.exp.__exit__(*a)
        self._caption()

    def track(self, project, step_fn, recorded_fn=None) -> "Section":
        self.project, self.step_fn, self.recorded_fn = project, step_fn, recorded_fn or (lambda s: {})
        self._caption()
        return self

    def status(self) -> tuple[str, str, object]:
        """(kind, tooltip, step) of this section; a container section takes the most urgent of its children."""
        from .. import badges  # noqa: PLC0415

        if self.children:
            sts = [c.status() for c in self.children]
            for kind in ("changed", "stale"):
                hits = [s for s in sts if s[0] == kind]
                if hits:
                    return kind, " ".join(h[1] for h in hits[:3]), hits[0][2]
            done = [s for s in sts if s[0] == "applied"]
            if done:
                newest = max(done, key=lambda s: s[2].timestamp)
                return "applied", (f"All {len(done)} applied operation(s) of this section are current (newest "
                                   f"applied {badges.when(newest[2])})."), newest[2]
            return "", "", None
        if self.step_fn is None:
            return "", "", None
        try:
            step = self.step_fn()
            if step is None:
                return "", "", None
            rec = self.recorded_fn(step) or {}
            kind, tip_ = badges.state(self.project, step, {k: self.fields[k].value for k in rec if k in self.fields},
                                      rec)
        except Exception:  # noqa: BLE001  (a badge never breaks the page)
            return "", "", None
        return kind, tip_, step

    def _caption(self) -> None:
        from .. import badges  # noqa: PLC0415

        kind, tip_, step = self.status()
        self.chip.set_visibility(bool(kind))
        if kind:
            self.chip.text = BADGE_TEXT[kind] + (f" {badges.when(step)}" if kind == "applied" and step else "")
            self.chip.classes(replace=BADGE_CLASS[kind])
            self.chip_tip.text = tip_
        parent = getattr(self, "parent", None)
        if parent is not None:
            parent._caption()

    def _reg(self, name: str, w, default):
        self.fields[name] = w
        self.defaults[name] = default
        if name in self.drafts:
            try:
                w.value = self.drafts[name]
            except Exception:  # noqa: BLE001
                self.drafts.pop(name, None)

        def changed(e, name=name):
            v = e.value if hasattr(e, "value") else w.value
            if v == self.defaults[name]:
                self.drafts.pop(name, None)
            else:
                self.drafts[name] = v
            self.gs.set("drafts", self.key, dict(self.drafts))
            self._caption()
        w.on_value_change(changed)
        return w

    def num(self, name: str, label: str, value, tipkey: str | None = None, step: float = 1.0, **kw):
        w = ui.number(label, value=value, step=step, **kw).classes("w-full").tooltip(tip(tipkey or name)) \
            .mark(f"{self.key}_{name}")
        return self._reg(name, w, value)

    def check(self, name: str, label: str, value: bool, tipkey: str | None = None):
        w = ui.checkbox(label, value=value).tooltip(tip(tipkey or name)).mark(f"{self.key}_{name}")
        return self._reg(name, w, value)

    def select(self, name: str, options, value, label: str, tipkey: str | None = None, **kw):
        w = ui.select(options, value=value, label=label, **kw).classes("w-full").tooltip(tip(tipkey or name)) \
            .mark(f"{self.key}_{name}")
        return self._reg(name, w, value)

    def text(self, name: str, label: str, value: str, tipkey: str | None = None):
        w = ui.input(label, value=value).classes("w-full").tooltip(tip(tipkey or name)).mark(f"{self.key}_{name}")
        return self._reg(name, w, value)

    def v(self, name: str):
        return self.fields[name].value

    def applied(self) -> None:
        self.drafts.clear()
        self.gs.pop("drafts", self.key)
        self._caption()


def ebsd_page() -> None:
    p = header("EBSD")
    if p is None:
        ui.navigate.to("/")
        return
    gs = GuiState(p)
    gs.set("tab", "/ebsd")
    check_interrupted(p)
    opts = map_options(p)
    sel = gs.get("selected_file")
    import uuid  # noqa: PLC0415

    st: dict = {"map": sel if sel in opts else next(iter(opts), None), "layer": None, "view": None, "busy": False,
                "mtab": "alpha", "ovsel": [], "legends": [], "pch": f"ebsd-{uuid.uuid4().hex[:8]}",
                "loading_ui": False, "selecting": False}
    viewer: dict = {"v": None}
    tab_vis: list = []                          # (element, tab) shown only on that map tab
    OP_NAMES = {"recon.run": "reconstruction", "grainsize.run": "grain size"}
    ui.add_css("""
      .ti64-pixelated img { image-rendering: pixelated; }
      .ti64-label span { font: 600 13px sans-serif; white-space: nowrap; text-shadow: 0 0 3px #fff; }
      .ti64-tip { font-size: 11px; padding: 0 4px; }
      .ti64-scalebar { background: rgba(255,255,255,.85); padding: 3px 6px 2px; border-radius: 4px; }
      .ti64-scalebar .bar { height: 5px; background: #000; }
      .ti64-scalebar .lab { font: 11px sans-serif; text-align: center; }
      /* the edit menu never grows wider than its 360 px column (a row of two selects with long labels did) */
      [data-testid="edit_menu"] .q-scrollarea__content { width: 100%; max-width: 100%; }
      .ti-ovgrid { display: grid; grid-auto-flow: column; grid-template-rows: repeat(4, auto); gap: 4px 18px;
                   padding: 10px 12px; }
    """)

    # ---- page layout ------------------------------------------------------------------------------------------
    with ui.row().classes("w-full no-wrap gap-0 items-stretch").style("height: calc(100vh - 56px)"):
        left = ui.column().classes("h-full gap-0 shrink-0 bg-white border-r").style("width: 360px") \
            .mark("ebsd_left")
        right = ui.column().classes("h-full grow gap-0 min-w-0")
    with left:
        menu = ui.scroll_area().classes("w-full grow").mark("edit_menu")
        busy_row = ui.row().classes("w-full items-center px-3 gap-2 no-wrap")
        with busy_row:
            prog = ui.linear_progress(show_value=False).props("indeterminate rounded color=primary") \
                .classes("grow").mark("apply_progress")
            busy_lab = ui.label().classes("text-xs ti-muted whitespace-nowrap")
        busy_row.set_visibility(False)
        with ui.row().classes("w-full p-3 gap-2 no-wrap border-t"):
            ui.button("Export", icon="ios_share", on_click=lambda: do_export()).classes("grow").mark("export_btn")
            cmp_btn = ui.button("Compare to original", icon="compare").props("outline").classes("grow") \
                .mark("compare_original").tooltip("Hold the button (mouse or Space / Enter) to see the unedited loaded "
                                                   "map in the same view; release to return.")
    # hold-to-compare, client-side only (no server round trip). The release listeners go on the document in the capture
    # phase: Quasar's button swallows keyup of Space / Enter, and the pointer may be released outside the button.
    release = "() => window.ti64Viewer && getElement(window.ti64Viewer)?.showAlt(false)"
    cmp_btn.on("pointerdown", js_handler="""(e) => {
        const v = window.ti64Viewer && getElement(window.ti64Viewer); if (!v) return;
        v.showAlt(true);
        const up = () => { v.showAlt(false); document.removeEventListener('pointerup', up, true);
                           document.removeEventListener('pointercancel', up, true); };
        document.addEventListener('pointerup', up, true); document.addEventListener('pointercancel', up, true); }""")
    cmp_btn.on("keydown", js_handler="""(e) => {
        if ((e.key !== ' ' && e.key !== 'Enter') || e.repeat) return;
        e.preventDefault();
        const v = window.ti64Viewer && getElement(window.ti64Viewer); if (!v) return;
        v.showAlt(true);
        const up = (u) => { if (u.key === ' ' || u.key === 'Enter') { v.showAlt(false);
                            document.removeEventListener('keyup', up, true); } };
        document.addEventListener('keyup', up, true); }""")
    cmp_btn.on("blur", js_handler=release)
    cmp_btn.on("pointerleave", js_handler="(e) => { if (e.buttons === 0) (" + release + ")(); }")

    with right:
        with ui.row().classes("w-full items-center gap-2 px-3 py-1 bg-white border-b").style("flex-wrap: wrap"):
            msel = ui.select(opts, value=st["map"], label="Sample / file").classes("w-64") \
                .props("dense options-dense").mark("ebsd_file").tooltip("Switch to another map of the project "
                                                                        "(grouped by sample)")
            kind = ui.select({}, label="map").classes("w-40").props("dense").mark("viewer_kind").tooltip(tip("layer"))
            ov_btn = ui.button("Overlays", icon="layers").props("outline dense no-caps no-wrap") \
                .mark("viewer_overlays")
            with ov_btn:
                ov_tip = ui.tooltip("Boundaries and crack paths on the map, with their colour and line width.")
                ov_menu = ui.menu().props("anchor='bottom left' self='top left'").mark("overlay_menu")
            with ui.row().classes("gap-0 no-wrap items-center").mark("legend_toggles"):
                lg_btns = {}
                for k, ic, lab in (("scale_bar", "straighten", "scale bar"), ("colorbar", "gradient",
                                                                                "colour bar / legend"),
                                   ("ipf_key", "donut_large", "IPF colour key")):
                    lg_btns[k] = ui.button(icon=ic, on_click=lambda k=k: toggle_legend(k)) \
                        .props("dense flat round").mark(f"legend_btn_{k}") \
                        .tooltip(f"Show / hide the {lab} on the map (drag it, resize it at its corner); exports show "
                                 "it exactly there")
                lay_btn = ui.button(icon="dashboard_customize").props("dense flat round").mark("legend_layout") \
                    .tooltip("Legend layout: snap the scale bar / colour bar / IPF key to a corner of the map, the "
                             "margin, auto size")
                with lay_btn:
                    lay_menu = ui.menu().props("anchor='bottom left' self='top left'").mark("legend_layout_menu")
            tool = ui.select(TOOLS, value="pan", label="tool").classes("w-32").props("dense").mark("viewer_tool")
            ui.space()
            hist_toggle = ui.button(icon="history", on_click=lambda: toggle_history()).props("flat round dense") \
                .mark("history_toggle").tooltip("Show / hide the History panel (right of the map)")
        mtabs = ui.tabs().classes("w-full bg-white border-b").props("dense align=left no-caps")
        with mtabs:
            t_alpha = ui.tab("alpha", label="α map", icon="map").mark("mtab_alpha")
            t_beta = ui.tab("beta", label="prior β", icon="hub").mark("mtab_beta")
            t_beta.set_visibility(False)                # shown once the map has a reconstruction
            ui.tab("results", label="Results", icon="insights").mark("rtab_results")
            ui.tab("pf", label="Pole figures", icon="radio_button_checked").mark("rtab_pf")
            ui.tab("ipf", label="Inverse pole figures", icon="change_history").mark("rtab_ipf")
            ui.tab("elastic", label="Elastic", icon="compress").mark("rtab_elastic")
        with ui.row().classes("w-full grow no-wrap gap-0 items-stretch min-h-0"):
            with ui.column().classes("grow gap-0 min-w-0 h-full") as map_col:
                holder = ui.column().classes("w-full grow gap-0 min-h-0").mark("map_holder")
                with ui.row().classes("w-full items-center no-wrap gap-3 px-3 bg-white border-t").style("height: 30px"):
                    status = ui.label("").classes("text-xs ti-muted truncate grow min-w-0").mark("viewer_status")
                    legend = ui.row().classes("items-center gap-2 no-wrap min-w-0 overflow-hidden shrink") \
                        .style("max-width: 55%").mark("viewer_legend")
            with ui.scroll_area().classes("grow h-full") as res_col:
                results = ui.column().classes("w-full gap-3 p-3").mark("results_panel")
                with results:
                    ui.label("Figures and tables of Apply / Draw appear here.").classes("text-sm ti-muted")
            res_col.set_visibility(False)
            with ui.scroll_area().classes("grow h-full") as tex_col:        # phase 6.7: PF / IPF / Elastic tabs
                tex_box = ui.column().classes("w-full gap-3 p-3").mark("texture_panel")
            tex_col.set_visibility(False)
            hist_col = ui.column().classes("h-full shrink-0 bg-white border-l gap-0").style("width: 290px") \
                .mark("history_panel")
            with hist_col:
                with ui.row().classes("w-full items-center px-3 py-2 border-b no-wrap"):
                    ui.icon("history", size="20px").classes("text-primary")
                    hist_title = ui.label("History").classes("font-semibold grow")
                hist_box = ui.column().classes("w-full grow gap-2 p-2 no-wrap min-w-0") \
                    .style("overflow-y: auto; overflow-x: hidden")
    hist_col.set_visibility(gs.get("history_open", default=True))

    def toggle_history():
        on = not hist_col.visible
        hist_col.set_visibility(on)
        gs.set("history_open", on)
        if viewer["v"]:
            viewer["v"].run_method("invalidate")

    if st["map"] is None:
        with holder:
            with ui.column().classes("p-8 gap-3"):
                ui.label("No EBSD map in this project yet.").classes("text-lg")
                ui.label("Add an .ang file on the Files tab; it loads here right away (sample and build direction can "
                         "be entered later, when a step needs them).")
                ui.button("Go to the Files tab", icon="folder", on_click=lambda: ui.navigate.to("/files"))
        return

    # ---- running steps -----------------------------------------------------------------------------------------
    async def run_step(op: str, params: dict, label: str, backend: str | None = None, with_map: bool = True,
                       channel: str | None = None):
        if st["busy"]:
            ui.notify("Another step is still running.", type="warning")
            return None
        st["busy"] = True
        busy_lab.text = f"{label} ({backend or p.info().backend})…"
        busy_row.set_visibility(channel is None)       # with a channel the map area shows the progress
        try:
            prm = {"map_id": st["map"], **params} if with_map else params
            # service.apply: re-applying the newest step of the map with new parameters replaces it (fix 5)
            replaced = service.tuning_target(p, op, prm) is not None
            if channel:                     # stage names of the work go to the map area's progress bar (fix 3.7)
                step = await ng_run.io_bound(progress.run_in, channel, service.apply, p, op, prm, backend)
            else:
                step = await ng_run.io_bound(service.apply, p, op, prm, backend)
            ui.notify(f"{label} done ({step.id}, {step.metrics.get('seconds')} s)" + (" — replaced the previous "
                      "step of the same operation" if replaced else ""), type="positive")
            for n in service.pop_notices():
                ui.notify(n, type="warning", multi_line=True, timeout=20000, close_button="OK")
            needs_hint(step)
            if hooks.get("history"):
                hooks["history"]()
            refresh_badges()
            return step
        except Exception as e:  # noqa: BLE001
            error(e, f"{label} failed")
            return None
        finally:
            st["busy"] = False
            busy_row.set_visibility(False)
    run_step = stable(run_step)

    def needs_hint(step) -> None:
        """A step that ran without a sheet value (e.g. the build direction) says what it left out, with a link."""
        f = step.outputs.get("summary")
        if f is None or f.kind != "json":
            return
        try:
            needs = json.loads((p.root / f.path).read_text(encoding="utf-8")).get("needs") or []
        except (OSError, ValueError):
            return
        if "build direction" in needs and step.op == "grainsize.run":
            sheet_missing(st["map"], f"The {OP_NAMES.get(step.op, step.op)} ran without the build direction of "
                                     f"{st['map']}: length / width along and across the build direction were not "
                                     "computed. Enter the build direction on the Files tab (section 'Map sheet') "
                                     "and run the step again.")

    def set_tab(t: str) -> None:
        map_col.set_visibility(t not in PANEL_TABS)
        res_col.set_visibility(t == "results")
        tex_col.set_visibility(t in ("pf", "ipf", "elastic"))
        if t in ("pf", "ipf", "elastic"):
            texture_panel(t)
        st["switching"] = True
        mtabs.value = t
        st["switching"] = False

    def show_image(path: Path, title: str, focus: bool = True, step=None) -> None:
        """A figure on the Results tab. Right-click: Delete (the figure step, undoable), Save (editable: MATLAB .fig,
        .svg / .pdf vector), Export (png / jpg / tif / svg / pdf)."""
        with results:
            with ui.column().classes("ti-card p-3 w-full gap-1").mark("result_card") as card:
                ui.label(title).classes("font-semibold")
                ui.image(str(path)).classes("w-full max-w-4xl").props("fit=contain")
                result_menu(card, title, step)
        if focus:
            set_tab("results")
        res_col.scroll_to(percent=1.0, duration=0.3)          # the newest figure / table is at the bottom

    def show_block(fn, title: str, focus: bool = True, step=None) -> None:
        with results:
            with ui.column().classes("ti-card p-3 w-full gap-1").mark("result_card") as card:
                ui.label(title).classes("font-semibold")
                fn()
                result_menu(card, title, step)
        if focus:
            set_tab("results")
        res_col.scroll_to(percent=1.0, duration=0.3)

    def result_menu(card, title: str, step) -> None:
        figure = step is not None and step.op in service.FIGURE_OPS
        with ui.context_menu().mark("result_menu"):
            if figure:
                ui.menu_item("Delete", on_click=lambda: delete_result(card, step)).mark("result_delete")
                be = step.backend or p.info().backend
                fmts = service.EDITABLE_FORMATS.get(be, ("svg", "pdf"))
                ui.menu_item("Save (" + ", ".join(f".{f_}" for f_ in fmts) + ")…",
                             on_click=lambda: save_result(step, title)).mark("result_save")
                ui.menu_item("Export (png, jpg, tif, svg, pdf)…",
                             on_click=lambda: export_result(step, title)).mark("result_export")
            else:
                ui.menu_item("Delete from Results", on_click=lambda: card.delete()).mark("result_delete")
                if step is not None:
                    d = p.find_step_dir(step.id)
                    if d is not None:
                        ui.menu_item("Save: show the table files (CSV / XLSX / JSON) in Explorer",
                                     on_click=lambda d=d: _reveal_dir(d)).mark("result_save")

    async def delete_result(card, step) -> None:
        try:
            service.delete_step(p, step.id)
        except KeyError:
            pass
        except Exception as e:  # noqa: BLE001
            error(e, "Could not delete the figure")
            return
        card.delete()
        ui.notify(f"Figure {step.id} deleted (also from History). Undo (Ctrl+Z) brings the step back.", type="positive")
        if hooks.get("history"):
            hooks["history"]()

    async def save_result(step, title: str) -> None:
        be = step.backend or p.info().backend
        fmts = service.EDITABLE_FORMATS.get(be, ("svg", "pdf"))
        types = {"fig": ("MATLAB figure", "*.fig"), "svg": ("SVG vector (editable)", "*.svg"),
                 "pdf": ("PDF vector", "*.pdf")}
        folder = gs.get("export_folder") or str(_pictures())
        f_ = await dialogs.save_file("Save figure (editable)", f"{st['map']}_{step.op.split('.')[0]}_{step.id}.{fmts[0]}",
                                     folder, [types[x] for x in fmts])
        if not f_:
            return
        fmt = Path(f_).suffix.lower().lstrip(".")
        if fmt not in fmts:
            f_, fmt = str(Path(f_).with_suffix("." + fmts[0])), fmts[0]
        gs.set("export_folder", str(Path(f_).parent))
        n = ui.notification(f"Saving {Path(f_).name} ({be}) …", spinner=True, timeout=None)
        try:
            await ng_run.io_bound(service.save_figure, p, step.id, fmt, f_)
            ui.notify(f"Saved {Path(f_).name} in {Path(f_).parent}", type="positive")
        except Exception as e:  # noqa: BLE001
            error(e, "Could not save the figure")
        finally:
            n.dismiss()

    def export_result(step, title: str, variant: str | None = None) -> None:
        folder = gs.get("export_folder") or str(_pictures())
        with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-full gap-3").mark("result_export_dialog"):
            ui.label(f"Export {title}").classes("text-lg font-semibold")
            fmt = ui.toggle({k: k.upper() for k in service.EXPORT_FORMATS}, value=gs.get("result_fmt") or "png") \
                .props("no-caps rounded unelevated toggle-color=primary").mark("rexp_fmt")
            dest = ui.input("Save as", value=str(Path(folder) / f"{st['map']}_{step.op.split('.')[0]}_{step.id}."
                                                f"{fmt.value}")).classes("w-full").mark("rexp_dest")
            fmt.on_value_change(lambda e: dest.set_value(str(Path(dest.value).with_suffix("." + e.value))))
            ui.label(f"Drawn again by {step.backend} with the figure's settings (Settings: width, DPI). Exports are "
                     "listed under 'Earlier exports' of the Export dialog.").classes("text-xs ti-muted")

            async def browse():
                f_ = await dialogs.save_file("Export as", Path(dest.value).name, str(Path(dest.value).parent),
                                             [(fmt.value.upper(), f"*.{fmt.value}")])
                if f_:
                    dest.value = f_

            async def go():
                t = Path(dest.value)
                if not t.parent.is_dir():
                    ui.notify(f"Folder {t.parent} does not exist.", type="warning")
                    return
                dlg.close()
                gs.set("export_folder", str(t.parent))
                gs.set("result_fmt", fmt.value)
                n = ui.notification(f"Exporting {t.name} …", spinner=True, timeout=None)
                try:
                    await ng_run.io_bound(service.export_result, p, step.id, fmt.value, t, variant)
                    ui.notify(f"Exported {t.name} to {t.parent}", type="positive")
                except Exception as e:  # noqa: BLE001
                    error(e, "Export failed")
                finally:
                    n.dismiss()
            with ui.row().classes("w-full justify-end"):
                ui.button("Browse…", icon="folder", on_click=browse).props("outline")
                ui.space()
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Export", icon="ios_share", on_click=go).mark("rexp_go")
        dlg.open()

    # ---- viewer helpers ----------------------------------------------------------------------------------------
    ipf_w: dict = {}
    vs: dict = {}

    def layer_params(source: str | None = None, k: str | None = None) -> lay.LayerParams:
        d: dict = {"kind": k or kind.value}
        if d["kind"] in ("ipf", "parent_ipf") and ipf_w:
            d["ipf_keys"] = {ph: IpfKeySetting(key=kk.value, direction=dd.value,
                                               custom_vector=tuple(float(c.value or 0) for c in vs["cv"])
                                               if dd.value == "custom" else None).model_dump(mode="json")
                             for ph, (kk, dd) in ipf_w.items()}
        if vs.get("cmin") is not None and vs["cmin"].value is not None and vs["cmax"].value is not None:
            d["clim"] = (float(vs["cmin"].value), float(vs["cmax"].value))
        if source:
            d["source"] = source
        if gs.get("highlight_ni") and not source:
            d["highlight_ni"] = True                  # cleanup section toggle (fix 5)
        return lay.LayerParams(**d)

    def tabkey() -> str:
        return st["mtab"] if st["mtab"] in ("alpha", "beta") else "alpha"

    # overlay styles (colour / width factor per class), per project
    def ov_style() -> dict:
        return dict(gs.get("overlay_style") or {})

    def refresh_overlay_menu(available: list[str]):
        ov_menu.clear()
        sty = ov_style()
        ov_btn.set_enabled(bool(available))
        st["ov_avail"] = bool(available)
        with ov_menu:
            ui.label("Overlays (tick to show; colour and line width per type)").classes("text-xs ti-muted px-3 pt-2")
            with ui.element("div").classes("ti-ovgrid").mark("overlay_grid"):
                for o in available:
                    s_ = sty.get(o, {})
                    with ui.row().classes("items-center no-wrap gap-1"):
                        cb = ui.checkbox(lay.OVERLAY_LABELS[o], value=o in st["ovsel"]).props("dense") \
                            .classes("min-w-[8.5rem]").mark(f"ov_{o}")
                        cb.on_value_change(lambda e, o=o: toggle_overlay(o, e.value))
                        if o != "burgers":
                            ci = ui.color_input(value=s_.get("color") or DEFAULT_OV_COLOR.get(o, "#000000"),
                                                preview=True).props("dense hide-bottom-space").classes("w-28") \
                                .mark(f"ov_color_{o}").tooltip("Line colour")
                            ci.on_value_change(lambda e, o=o: set_ov_style(o, color=e.value or None))
                        wn = ui.number(value=s_.get("width", 1.0), min=0.25, max=10, step=0.25, format="%g") \
                            .props("dense hide-bottom-space").classes("w-20").mark(f"ov_width_{o}") \
                            .tooltip("Line width factor (1 = default). On the map the line keeps this width in screen "
                                     "pixels at every zoom ('overlay line width [px]' in 'Maps and IPF key' x factor); "
                                     "exports use the figure line width x factor.")
                        wn.on_value_change(lambda e, o=o: set_ov_style(o, width=float(e.value or 1)))
            if not available:
                ui.label("No overlays yet: boundaries appear after 'Grains and boundaries' (α map) or the "
                         "reconstruction (prior β tab); crack paths after drawing one.").classes("text-xs px-3 pb-2")
        ov_label()

    def ov_label():
        """'Overlays (2)' on the button (one line); the names in its tooltip."""
        names = ", ".join(lay.OVERLAY_LABELS[o] for o in st["ovsel"])
        ov_btn.text = f"Overlays ({len(st['ovsel'])})" if st["ovsel"] else "Overlays"
        if not st.get("ov_avail", True):
            ov_tip.text = ("No overlays yet: grain boundaries appear after 'Grains and boundaries' (α map), prior-β "
                           "boundaries after the reconstruction (prior β tab), crack paths after drawing one.")
            return
        ov_tip.text = (f"Shown: {names}. " if names else "No overlay shown. ") +             "Boundaries and crack paths on the map, with their colour and line width."

    async def toggle_overlay(o: str, on: bool):
        cur = [x for x in st["ovsel"] if x != o] + ([o] if on else [])
        st["ovsel"] = [x for x in lay.OVERLAYS if x in cur]
        ov_label()
        await show_overlays()

    async def set_ov_style(o: str, **kw):
        sty = ov_style()
        cur = dict(sty.get(o, {}))
        cur.update(kw)
        sty[o] = cur
        gs.set("overlay_style", sty)
        if o in st["ovsel"]:
            await show_overlays()

    # legends on the map (WYSIWYG for exports)
    def legend_items() -> list[dict]:
        """What is drawn on the map: the IPF key only on IPF maps, a colour bar / legend only on maps that have one
        (an IPF key switched on is shown as the colour bar of a scalar map in the same place)."""
        ipf = kind.value in IPF_KINDS
        items = list(st["legends"])
        if not ipf and any(i["kind"] == "ipf_key" for i in items) and not any(i["kind"] == "colorbar" for i in items):
            k = next(i for i in items if i["kind"] == "ipf_key")
            items.append({**default_item("colorbar").model_dump(), "anchor": k.get("anchor"),
                          "margin": k.get("margin"), "x": k["x"], "y": k["y"]})
        return [i for i in items if not (i["kind"] == "ipf_key" and not ipf) and not (i["kind"] == "colorbar" and ipf)]

    def save_legends():
        gs.set("legends", st["map"], tabkey(), st["legends"])
        upd_legend_buttons()
        refresh_layout_menu()

    def upd_legend_buttons():
        ipf = kind.value in IPF_KINDS
        for k, b in lg_btns.items():
            on = any(i["kind"] == k for i in st["legends"])
            usable = not ((k == "ipf_key" and not ipf) or (k == "colorbar" and ipf))
            b.props(f"color={'primary' if on and usable else 'grey-7'}")
            b.set_enabled(usable)
        lg_btns["ipf_key"].set_visibility(ipf)
        lg_btns["colorbar"].set_visibility(not ipf)

    def refresh_layout_menu():
        lay_menu.clear()
        shown = legend_items()
        with lay_menu, ui.column().classes("p-3 gap-2 w-80").mark("legend_layout_body"):
            ui.label("Legend layout").classes("font-semibold")
            if not shown:
                ui.label("No legend on this map: switch one on with the buttons left of this one.") \
                    .classes("text-xs ti-muted")
            names = {"scale_bar": "scale bar", "colorbar": "colour bar / legend", "ipf_key": "IPF key"}
            for it in shown:
                with ui.row().classes("w-full items-end no-wrap gap-2"):
                    ui.label(names[it["kind"]]).classes("w-24 text-sm pb-2")
                    ui.select(ANCHOR_LABELS, value=it.get("anchor") or "", label="position").props("dense") \
                        .classes("grow").mark(f"legend_anchor_{it['kind']}") \
                        .on_value_change(lambda e, k=it["kind"]: set_legend(k, anchor=e.value or None))
                    ui.checkbox("auto size", value=bool(it.get("auto"))).props("dense") \
                        .mark(f"legend_auto_{it['kind']}") \
                        .tooltip("Width follows the map (scale bar 22 %, colour bar 30 %, IPF key 18 % of the map "
                                 "width). Resizing it at its blue corner switches this off.") \
                        .on_value_change(lambda e, k=it["kind"]: set_legend(k, auto=bool(e.value)))
            m0 = next((i.get("margin") for i in st["legends"] if i.get("margin") is not None), None)
            ui.number("margin to the map edge [% of the map width]", value=round(100 * (m0 if m0 is not None else 0.02),
                                                                                    2),
                      min=0, max=25, step=0.5).props("dense").classes("w-full").mark("legend_margin") \
                .on_value_change(lambda e: set_legend(None, margin=max(0.0, float(e.value or 0)) / 100))
            ui.label("Dragging a legend near a corner or an edge centre of the map snaps it there.") \
                .classes("text-xs ti-muted")

    def set_legend(k: str | None, **upd):
        """Change anchor / auto / margin of one legend (k) or the margin of all (k None) and redraw."""
        if "auto" in upd and upd["auto"]:
            from ..legends import AUTO_W  # noqa: PLC0415

            upd["w"] = AUTO_W.get(k, 0.2)
        st["legends"] = [{**i, **upd} if (k is None or i["kind"] == k) else i for i in st["legends"]]
        if k is not None and not any(i["kind"] == k for i in st["legends"]):      # the colour bar shown in place of
            st["legends"] = st["legends"] + [{**default_item(k).model_dump(), **upd}]   # the IPF key
        gs.set("legends", st["map"], tabkey(), st["legends"])
        upd_legend_buttons()
        if viewer["v"]:
            viewer["v"].set(legends=legend_items())

    async def toggle_legend(k: str):
        on = not any(i["kind"] == k for i in st["legends"])
        if not on:
            st["legends"] = [i for i in st["legends"] if i["kind"] != k]
        else:
            st["legends"] = st["legends"] + [default_item(k).model_dump()]
        save_legends()
        if viewer["v"]:
            viewer["v"].set(legends=legend_items())
            if on and k == "ipf_key" and st["layer"] is not None:     # the key is drawn by the backend on demand
                viewer["v"].set(legend_data=await legend_data(st["layer"]))

    def on_legends(e):
        got = [{**dict(i), "frame": "map"} for i in e.args.get("items", [])]
        kinds = {i["kind"] for i in got}
        # items hidden on this map (IPF key on a scalar map ...) are kept as they were
        st["legends"] = got + [i for i in st["legends"] if i["kind"] not in kinds]
        gs.set("legends", st["map"], tabkey(), st["legends"])
        upd_legend_buttons()
        if viewer["v"]:
            viewer["v"]._props["legends"] = legend_items()

    async def legend_data(L: lay.Layer, with_key: bool = True) -> dict:
        info = L.info
        d: dict = {"colorbar": None, "swatches": [], "key_url": ""}
        if info.get("colorbar"):
            d["colorbar"] = {"stops": info["colorbar"][:: max(1, len(info["colorbar"]) // 32)],
                             "lo": (info.get("clim") or [None, None])[0], "hi": (info.get("clim") or [None, None])[1],
                             "label": info.get("colorbar_label") or ""}
        elif info.get("legend"):
            d["swatches"] = [list(x) for x in info["legend"][:14]]
        if with_key and kind.value in IPF_KINDS and any(i["kind"] == "ipf_key" for i in st["legends"]):
            try:
                pngs = await ng_run.io_bound(lay.key_pngs, p, st["map"], layer_params())
                if pngs:
                    d["key_url"] = key_url(pngs)
            except Exception as e:  # noqa: BLE001
                error(e, "Could not draw the IPF key")
        return d

    def refresh_annotations():
        items = load_annotations(p, st["map"])
        if viewer["v"]:
            viewer["v"].set(shapes=shapes_of(items))
        if "ann_list" in vs:
            ann_list = vs["ann_list"]
            ann_list.clear()
            with ann_list:
                if not items:
                    ui.label("No annotations yet. Pick a drawing tool in the 'tool' list of the map toolbar.").classes("text-xs ti-muted")
                for a in items:
                    with ui.row().classes("items-center gap-1 no-wrap w-full"):
                        ui.label(f"{a.type} {a.text}".strip()).classes("text-sm grow truncate")
                        if a.type in ROI_TYPES:
                            b = roi_box(a)
                            ui.button(icon="zoom_in", on_click=lambda b=b: viewer["v"] and viewer["v"].run_method(
                                "zoomTo", *b)).props("dense flat round size=sm").tooltip("Zoom to this ROI")
                            ui.button(icon="content_cut", on_click=lambda a=a: crop_dialog(a)) \
                                .props("dense flat round size=sm").tooltip("Crop the map to this ROI (new map)")
                        ui.button(icon="delete", on_click=lambda a=a: delete_annotation(a.id)) \
                            .props("dense flat round size=sm").tooltip("Delete").mark(f"del_{a.id}")

    async def save_items(items: list[Annotation], label: str):
        step = await run_step("annotations.set", {"items": [a.model_dump(mode="json") for a in items]}, label)
        if step:
            refresh_annotations()
            refresh_overlay_menu(lay.available(p, st["map"], tabkey())["overlays"])
            if "crack" in st["ovsel"]:
                await show_overlays()

    async def delete_annotation(aid: str):
        await save_items([a for a in load_annotations(p, st["map"]) if a.id != aid], "annotation deleted")

    async def on_shape(e):
        d = e.args
        t, xy = d["type"], [tuple(q) for q in d["xy"]]
        text = vs["ann_text"].value or "" if "ann_text" in vs else ""
        if t == "text" and not text:
            ui.notify("Enter the text first (Annotations section, field 'text / name').", type="warning")
            return
        if t in ROI_TYPES and not text:
            text = f"ROI{1 + sum(a.type in ROI_TYPES for a in load_annotations(p, st['map']))}"
        a = Annotation(map_id=st["map"], type=t, xy=xy, text=text,
                       color=(vs["ann_color"].value or "") if "ann_color" in vs else "",
                       width_pt=float((vs["ann_w"].value if "ann_w" in vs else 1.5) or 1.5))
        await save_items(load_annotations(p, st["map"]) + [a], f"{t} added")

    def crop_dialog(a: Annotation):
        with ui.dialog() as dlg, ui.card().classes("w-[30rem] max-w-full"):
            ui.label(f"Crop {st['map']} to ROI {a.text or a.id}").classes("text-lg font-semibold")
            ui.label("Writes maps/<new id>.ang in the project (rows inside the ROI's bounding box) and links it to the "
                     "same sample with this map's sheet.").classes("text-sm ti-muted")
            nid = ui.input("New map ID", value=f"{st['map']}_{a.text or a.id}").classes("w-full")

            async def go():
                dlg.close()
                step = await run_step("ebsd.crop", {"new_map_id": nid.value, "roi_id": a.id}, "crop")
                if step:
                    ui.notify(f"Map {nid.value}: {step.metrics.get('n_points')} points; pick it in the Sample / file "
                              "list.", type="positive")
                    msel.options = map_options(p)
                    msel.update()
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Crop", icon="content_cut", on_click=go).mark("crop_go")
        dlg.open()

    def on_probe(e):
        x, y = e.args["xy"][0]
        L = st["layer"].layout
        i = int(L.sample(np.array([x]), np.array([y]))[0])
        if i < 0:
            status.text = f"x {x:.2f} µm, y {y:.2f} µm: no measurement"
            return
        # fix 5: like the OIM status bar: file Euler angles in degrees (as in the .ang / OIM), the app-frame Euler
        # angles after the EDAX load setting (labelled), scan row / column, IQ, CI
        from ..probe import probe, probe_text  # noqa: PLC0415

        try:
            d_ = probe(p, st["map"], st["layer"].info["source"], i)
        except Exception as ex:  # noqa: BLE001
            status.text = f"x {x:.2f} µm, y {y:.2f} µm | point {i} (probe failed: {ex})"
            return
        c = st["layer"].colors[i]
        status.text = probe_text(d_) + f" | colour #{c[0]:02x}{c[1]:02x}{c[2]:02x}"

    def on_measure(e):
        (x0, y0), (x1, y1) = e.args["xy"]
        d = float(np.hypot(x1 - x0, y1 - y0))
        ang = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
        status.text = f"distance {d:.2f} µm, angle from +x toward +y {ang:.1f}°  ({x0:.1f}, {y0:.1f}) → ({x1:.1f}, {y1:.1f})"
        ui.notify(f"{d:.2f} µm", type="info")

    def on_view(e):
        st["view"] = e.args
        gs.set("view", st["map"], {k: e.args[k] for k in ("x0", "x1", "y0", "y1")})

    def show_legend(L: lay.Layer):
        legend.clear()
        info = L.info
        with legend:
            if info.get("colorbar"):
                stops = ", ".join(info["colorbar"][:: max(1, len(info["colorbar"]) // 32)])
                lo, hi = info["clim"]
                ui.label(f"{lo:.3g}").classes("text-xs")
                ui.html(f'<div style="height:12px;width:160px;border-radius:3px;background:linear-gradient(to right,{stops})"></div>') \
                    .mark("viewer_colorbar")
                ui.label(f"{hi:.3g} {info.get('colorbar_label') or ''}").classes("text-xs")
            for lab, col in (info.get("legend") or [])[:6]:
                ui.html(f'<div style="width:12px;height:10px;background:{col};border:1px solid #999"></div>')
                ui.label(lab).classes("text-xs")
            if info.get("keys"):
                ui.label(" · ".join(f"{ph.replace('Ti ', '')}: {k['key']} ∥ {k['direction']}"
                                    for ph, k in info["keys"].items())).classes("text-xs ti-muted")
            ui.label(f"coloured by {info.get('backend')}").classes("text-xs ti-muted")

    async def show_overlays():
        if st["layer"] is None or viewer["v"] is None:
            return
        ovs = []
        lp = layer_params()
        if tabkey() == "beta":                  # HAGB / LAGB of the prior-beta grains (fix 3.10)
            lp.grains = lay.newest_grains(p.recipe().steps, st["map"], "beta")
        lw = float((vs["lw"].value if "lw" in vs else 1.0) or 1.0)
        sty = ov_style()
        for name in st["ovsel"]:
            try:
                O = await ng_run.io_bound(lay.get_overlay, p, st["map"], name, lp, None, sty.get(name))
                ovs.append({"url": tiles.overlay_url(tiles.register_overlay(O, st["layer"].layout), lw), "opacity": 1})
            except Exception as e:  # noqa: BLE001
                error(e, f"overlay {name}")
        viewer["v"].set(overlays=ovs)
        gs.set("overlays", st["map"], tabkey(), list(st["ovsel"]))

    async def prepare_original(L: lay.Layer):
        """Layer of the unedited loaded map (same kind if possible) for Compare to original."""
        load = service.latest_step(p, ("ebsd.load",), map_id=st["map"])
        if load is None or viewer["v"] is None:
            return
        if L.info.get("source") == load.id:
            viewer["v"].set(alt_layer_url=tiles.layer_url(tiles.register_layer(L)))
            return
        k = kind.value if kind.value in ("ipf", "phase", "ci", "iq", "fit", *lay.PRIAS_KINDS) else "ipf"
        try:
            O = await ng_run.io_bound(lay.get_layer, p, st["map"], layer_params(source=load.id, k=k))
            if viewer["v"] is not None:
                viewer["v"].set(alt_layer_url=tiles.layer_url(tiles.register_layer(O)))
        except Exception:  # noqa: BLE001
            pass

    async def show():
        if not kind.value:
            return
        ch = st["pch"]
        if viewer["v"] is None and not st["loading_ui"]:       # first image of this map: progress in the map area
            loading(st["map"], "Drawing")
        progress.set(ch, "colouring map")
        try:
            L = await ng_run.io_bound(progress.run_in, ch, lay.get_layer, p, st["map"], layer_params())
        except Exception as e:  # noqa: BLE001
            progress.clear(ch)
            st["loading_ui"] = False
            from ..sample import SheetIncomplete  # noqa: PLC0415

            # never leave the previous map (e.g. of the other map tab) on screen under this tab's labels
            if isinstance(e, SheetIncomplete):
                not_ready(f"{kind.options.get(kind.value, kind.value)}: {e}", "Open map sheet",
                          lambda: ui.navigate.to(f"/files?sel={st['map']}"))
            else:
                not_ready(f"The {kind.options.get(kind.value, kind.value)} map could not be drawn (the message says "
                          "why).")
            error(e, f"Could not colour the {kind.value} map")
            return
        if L is None:                                 # app shutting down (io_bound returns None)
            return
        st["layer"] = L
        gs.set("layer", st["map"], tabkey(), kind.value)
        ext = list(L.layout.extent)
        progress.set(ch, "drawing")
        if viewer["v"] is None or viewer["v"]._props["base"] != L.layout.cw:
            holder.clear()
            st["loading_ui"] = False
            with holder:
                v = MapViewer(L.layout.cw, ext).classes("w-full h-full").mark("map_viewer")
                v._props["initial_view"] = gs.get("view", st["map"])
                v.on("shape", on_shape)
                v.on("probe", on_probe)
                v.on("measure", on_measure)
                v.on("view", on_view)
                v.on("legends", on_legends)
                v.on("compare", lambda e: status.set_text("showing the ORIGINAL loaded map (release to return)"
                                                          if e.args.get("on") else ""))
                v.on("shape_click", lambda e: status.set_text(f"annotation {e.args['id']}"))
                viewer["v"] = v
            ui.run_javascript(f"window.ti64Viewer = {v.id}")
        viewer["v"].set(layer_url=tiles.layer_url(tiles.register_layer(L)), extent=ext,
                        layer_opacity=float((vs["opacity"].value if "opacity" in vs else 1.0) or 1.0),
                        tool=tool.value, legends=legend_items(), legend_data=await legend_data(L, with_key=False),
                        hex_grid=bool(L.layout.hex), cell_um=float(L.layout.dx))
        progress.clear(ch)
        upd_legend_buttons()
        refresh_layout_menu()
        show_legend(L)
        refresh_annotations()
        await show_overlays()
        if kind.value in IPF_KINDS and any(i["kind"] == "ipf_key" for i in st["legends"]) and viewer["v"]:
            # the IPF key after the map is on screen (first MATLAB figure render of an engine takes ~45 s, fix 3.8)
            viewer["v"].set(legend_data=await legend_data(L))
        status.text = (f"{st['map']}: {L.layout.lut.shape[0]} rows, {'hex' if L.layout.hex else 'square'} grid, "
                       f"step {L.layout.dx:.3g} µm; {L.info.get('title', '')}")
        sk._caption()                                  # the KAM section compares with the kind shown (fix 5)
        await prepare_original(L)

    def not_ready(text: str, button: str | None = None, action=None):
        holder.clear()
        viewer["v"] = None
        with holder:
            with ui.column().classes("p-8 gap-3"):
                ui.label(text).classes("text-base").mark("viewer_placeholder")
                if button:
                    ui.button(button, on_click=action).mark("placeholder_btn")

    async def select_map(m: str, draw: bool = True, tab: str | None = None, autoload: bool = True):
        st["map"] = m
        gs.set("selected_file", m)
        sheet_info()
        try:
            p.sample(m)
        except (KeyError, ValueError) as e:
            not_ready(str(e).strip("'\""), "Go to the Files tab", lambda: ui.navigate.to("/files"))
            kind.options = {}
            kind.update()
            refresh_overlay_menu([])
            return
        av = lay.available(p, m)
        if not av["loaded"] and autoload:
            # fix 2: the selected map loads by itself (progress bar under the edit menu); sample and build direction
            # are not needed for loading
            try:                                   # fix 4: phases the crystal symmetry cannot tell apart
                from ..phases import for_file  # noqa: PLC0415

                sh = p.sample(m)
                if sh.map_file.lower().endswith(".ang"):
                    for_file(sh.map_file, sh.phase_map, m)
            except Exception as e:  # noqa: BLE001
                from ..sample import SheetIncomplete  # noqa: PLC0415

                if isinstance(e, SheetIncomplete):
                    not_ready(str(e), "Open map sheet", lambda: ui.navigate.to(f"/files?sel={m}"))
                    return
            from . import indexing  # noqa: PLC0415

            indexing.ask_pending(p, on_done=sheet_info, only=[m])      # fix 5: re-indexed with spherical indexing?
            loading(m)
            step = await run_step("ebsd.load", {}, f"loading {m}", channel=st["pch"])
            if step is not None:
                frame_notice(step)
            if st["map"] != m:                     # the user picked another map meanwhile
                return
            if step is None:
                st["loading_ui"] = False
                progress.clear(st["pch"])
                not_ready(f"{m} could not be loaded (the message says why).", "Try again",
                          lambda: select_map(m, draw, tab))
                return
            av = lay.available(p, m)
        has_beta = av["recon"]
        t_beta.set_visibility(has_beta)
        t = tab or (mtabs.value if mtabs.value in ("alpha", "beta") else None) or gs.get("mtab", m) or "alpha"
        if t == "beta" and not has_beta:
            t = "alpha"
        st["mtab"] = t
        gs.set("mtab", m, t)
        set_tab(t)
        kinds = [k for k in av["layers"] if (k in lay.RECON_KINDS) == (t == "beta")]
        if t == "alpha" and av["loaded"]:           # KAM / GROD / GOS / GND listed, computed on first use (fix 3.9)
            kinds += [k for k in ON_DEMAND if k not in kinds]
        update_tab_parts(t, av)
        kind.options = kind_labels(kinds, av["layers"])
        saved = gs.get("layer", m)
        saved = saved.get(t) if isinstance(saved, dict) else (saved if t == "alpha" else None)
        if saved in ON_DEMAND and saved not in av["layers"]:
            saved = None                             # never computed on its own (e.g. stale): start with IPF
        st["selecting"] = True
        try:
            kind.set_value(saved if saved in kinds else ("parent_ipf" if t == "beta" and "parent_ipf" in kinds
                                                          else (kinds[0] if kinds else None)))
        finally:
            st["selecting"] = False
        kind.update()
        ov_av = lay.available(p, m, t)["overlays"]
        saved_ov = gs.get("overlays", m)
        # the prior-beta map shows its prior-beta grain boundaries (parent grains) until the user unticks them
        saved_ov = saved_ov.get(t) if isinstance(saved_ov, dict) and t in saved_ov else \
            (saved_ov if t == "alpha" and isinstance(saved_ov, list) else (["beta_gb"] if t == "beta" else []))
        st["ovsel"] = [o for o in (saved_ov or []) if o in ov_av]
        refresh_overlay_menu(ov_av)
        st["legends"] = [dict(i) for i in (gs.get("legends", m, t) or [default_item("scale_bar").model_dump()])]
        gs.set("legends", m, t, st["legends"])
        hist_title.text = f"History · {'prior β map' if t == 'beta' else 'α map'}"
        if hooks.get("history"):
            hooks["history"]()
        refresh_badges()
        if not av["loaded"]:
            not_ready(f"{m} is not loaded yet.", "Load map", lambda: load_section_apply())
            return
        if draw:
            await show()

    def loading(m: str, what: str = "Loading") -> None:
        """Progress in the map area (fix 3.7): bar + the stage running now (reading file / building grid / cleanup
        from cache / colouring map / drawing) and the time of the stages done."""
        import time  # noqa: PLC0415

        holder.clear()
        viewer["v"] = None
        ch = st["pch"]
        progress.clear(ch)
        progress.set(ch, "starting")
        st["loading_ui"] = True
        t0 = time.perf_counter()
        with holder:
            with ui.column().classes("w-full h-full items-center justify-center gap-2").mark("viewer_loading"):
                ui.label(f"{what} {m}").classes("text-lg font-semibold")
                bar = ui.linear_progress(value=0.02, show_value=False, size="10px") \
                    .props("rounded color=primary animation-speed=250").classes("w-[28rem] max-w-full").mark("load_progress")
                stage = ui.label("starting …").classes("text-sm").mark("load_stage")
                done = ui.label("").classes("text-xs ti-muted text-center").mark("load_stages_done")

            def tick():
                d = progress.get(ch)
                if not d:
                    return
                f_ = d.get("frac")
                bar.value = max(bar.value or 0, f_ if f_ is not None else STAGE_FRAC.get(d["stage"], bar.value or 0))
                stage.text = f"{d['stage']} …  ({time.perf_counter() - t0:.0f} s)"
                done.text = "  ·  ".join(f"{s_} {t_:.1f} s" for s_, t_ in progress.timings(ch)[:-1]
                                         if s_ != "starting")
            ui.timer(0.25, tick)

    def kind_labels(kinds: list[str], done: list[str]) -> dict[str, str]:
        return {k: lay.LAYER_LABELS[k] + ("" if k in done or k not in ON_DEMAND else " · computed on first use")
                for k in kinds}

    def update_tab_parts(t: str, av: dict) -> None:
        """Edit-menu parts that belong to one map tab, and figure lists that only offer what exists (fix 8 / 9)."""
        for el, tb in tab_vis:
            el.set_visibility(tb == t)
        sg.title_label.text = "Grains and boundaries · " + ("prior β" if t == "beta" else "α map")
        gs_title.text = "Grain size · " + ("prior β" if t == "beta" else "α map")
        kinds = [k for k in GS_FIG_KINDS if (k in GS_BETA_KINDS) == (t == "beta")]
        gfk.options = {k: GS_FIG_LABELS[k] for k in kinds}
        if gfk.value not in kinds:
            sz.defaults["fig"] = kinds[0]          # switching the map tab is not an unapplied edit
            gfk.value = kinds[0]
        gfk.update()
        gs_fig_row.set_visibility(bool(av.get("grainsize")))
        recon_figs.set_visibility(bool(av.get("recon")))

    # ---- edit menu ---------------------------------------------------------------------------------------------
    def lastp(op: str) -> dict:
        s = service.latest_step(p, (op,), map_id=st["map"])
        return s.params if s else {}

    def g(d, *keys, default=None):
        for k in keys:
            if not isinstance(d, dict) or d.get(k) is None:
                return default
            d = d[k]
        return d
    # the edit menu starts from the values of the last applied step of this map (else the defaults)
    lg = lastp("grains.calc")

    def last_kind(*kinds):
        for s_ in reversed(p.recipe().steps):
            if s_.op == "map.compute" and not s_.stale and s_.params.get("map_id") == st["map"] \
                    and s_.params.get("kind") in kinds:
                return s_.params
        return {}
    lk, lgnd = last_kind("kam"), last_kind("gnd")
    lr, ls = lastp("recon.run").get("params") or {}, lastp("grainsize.run")
    hooks: dict = {"history": None}

    def roi_options() -> dict:
        """ROIs drawn on this map (annotations), for the texture / elastic region."""
        try:
            return {a.id: f"ROI {a.text or a.id}" for a in load_annotations(p, st["map"]) if a.type in ROI_TYPES}
        except Exception:  # noqa: BLE001
            return {}

    with menu:
        with ui.column().classes("w-full gap-0"):
            with ui.row().classes("w-full items-center no-wrap gap-1 px-4 pt-2 min-w-0"):
                sheet_lab = ui.label().classes("text-xs ti-muted grow min-w-0").style("overflow-wrap: anywhere") \
                    .mark("sheet_info")
                sheet_link = ui.button("Map sheet", icon="edit_note",
                                       on_click=lambda: ui.navigate.to(f"/files?sel={st['map']}")) \
                    .props("flat dense no-caps size=sm color=warning").classes("shrink-0").mark("open_sheet") \
                    .tooltip("Open this map's sheet on the Files tab: sample or build direction not entered yet (the "
                             "map loads without them; steps that need a value ask for it)")

            # 1 load (fix 5: Load only loads; cleanup is its own section below)
            with Section(gs, "load", "Load", "download", opened=True) as sl:
                load_info = ui.label("").classes("text-xs ti-muted").style("overflow-wrap: anywhere") \
                    .mark("load_info")

                async def load_section_apply():
                    step = await run_step("ebsd.load", {}, "load map")
                    if step:
                        frame_notice(step)
                        await select_map(st["map"])
                ui.button("Load again", icon="download", on_click=load_section_apply).props("outline") \
                    .mark("run_load").tooltip("The selected map loads by itself when it is opened; this loads it again "
                                              "(e.g. after the input state was changed). Pressed right after a load, "
                                              "it replaces that load step instead of adding one.")
            sl.track(p, lambda: service.latest_step(p, ("ebsd.load",), map_id=st["map"]))

            # 2 cleanup: one sub-panel per operation, each its own recipe step on the previous cleanup step's output
            with Section(gs, "cleanup", "Cleanup", "cleaning_services") as sc:
                ui.label("Each operation is its own step on the output of the previous cleanup step of this map, so "
                         "you decide the order (History shows it; drag a step there to change the order, the chain "
                         "is replayed). Applying the same operation again right after itself replaces that step.") \
                    .classes("text-xs ti-muted")
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.button("Add default cleanup", icon="playlist_add", on_click=lambda: add_default_cleanup()) \
                        .props("outline no-caps").mark("add_default_cleanup") \
                        .tooltip("Adds the preset of the input state as separate steps in the default order: raw = "
                                 "CI threshold 0.1, noise-grain removal (3°, ≤ 2 px, filled), void fill (≤ 4 px); "
                                 "OIM-cleaned = noise-grain removal, void fill. Ctrl+Z takes all of them back at once.")
                hl = ui.checkbox("highlight not-indexed points", value=bool(gs.get("highlight_ni"))) \
                    .props("dense").mark("highlight_ni") \
                    .tooltip("Draw the not-indexed points magenta on the map (IPF, phase, CI, IQ …), e.g. to see "
                             "which voids a fill left (voids larger than the maximum void size stay not indexed)")

                async def on_hl(e):
                    gs.set("highlight_ni", bool(e.value))
                    await show()
                hl.on_value_change(on_hl)
                clean_secs: dict[str, Section] = {}
                clean_sum: dict[str, ui.label] = {}
                for op_, icon_, flds in CLEAN_UI:
                    last_ = service.latest_step(p, ("ebsd.clean",), map_id=st["map"], operation=op_)
                    lp_ = (last_.params.get("params") if last_ else None) or {}
                    with Section(gs, f"clean_{op_}", CLEAN_LABELS[op_], icon_, group=None, sub=True) as ss:
                        for name_, kind_, label_, default_, kw_ in flds:
                            v_ = lp_.get(name_, default_)
                            if kind_ == "num":
                                ss.num(name_, label_, v_, f"clean_{name_}", **kw_)
                            else:
                                ss.check(name_, label_, bool(v_), f"clean_{name_}")
                        clean_sum[op_] = ui.label("").classes("text-xs").style("overflow-wrap: anywhere") \
                            .mark(f"clean_summary_{op_}")
                        ui.button("Apply", icon="check", on_click=lambda op_=op_: apply_clean(op_)) \
                            .props("dense").mark(f"run_clean_{op_}")
                    ss.track(p, lambda op_=op_: service.latest_step(p, ("ebsd.clean",), map_id=st["map"],
                                                                    operation=op_),
                             lambda s_: dict(s_.params.get("params") or {}))
                    ss.parent = sc
                    sc.children.append(ss)
                    clean_secs[op_] = ss

                async def apply_clean(op_: str):
                    ss = clean_secs[op_]
                    if not lay.available(p, st["map"])["loaded"]:
                        await load_section_apply()
                    prm = {k: ss.v(k) for k in ss.fields}
                    for k in INT_FIELDS & set(prm):
                        prm[k] = int(prm[k] or 0)
                    step = await run_step("ebsd.clean", {"operation": op_, "params": prm}, CLEAN_LABELS[op_])
                    if step:
                        ss.applied()
                        summ = clean_summaries()
                        if summ.get(op_):
                            ui.notify(f"{CLEAN_LABELS[op_]}: {summ[op_]}", type="info", multi_line=True,
                                      timeout=12000)
                        await select_map(st["map"])

                async def add_default_cleanup():
                    if st["busy"]:
                        ui.notify("Another step is still running.", type="warning")
                        return
                    if not lay.available(p, st["map"])["loaded"]:
                        await load_section_apply()
                    st["busy"] = True
                    busy_lab.text = "default cleanup …"
                    busy_row.set_visibility(True)
                    try:
                        steps_ = await ng_run.io_bound(service.add_default_cleanup, p, st["map"])
                        ui.notify(f"Default cleanup added: {len(steps_)} steps "
                                  f"({', '.join(CLEAN_LABELS[s_.params['operation']] for s_ in steps_)})",
                                  type="positive")
                    except Exception as e:  # noqa: BLE001
                        error(e, "Default cleanup failed")
                    finally:
                        st["busy"] = False
                        busy_row.set_visibility(False)
                    if hooks.get("history"):
                        hooks["history"]()
                    await select_map(st["map"])
            sc.project = p

            def clean_summaries() -> dict[str, str]:
                """The report line of the newest step of each cleanup operation of this map (removed / filled /
                unfilled and why), shown under its Apply button."""
                from .history_panel import step_summary  # noqa: PLC0415

                out = {}
                for op_ in clean_secs:
                    s_ = service.latest_step(p, ("ebsd.clean",), map_id=st["map"], operation=op_)
                    out[op_] = step_summary(p, s_) if s_ else ""
                    clean_sum[op_].text = (f"Last applied: {out[op_]}" if out[op_] else "")
                return out

            # 2 maps and IPF key
            with Section(gs, "ipf", "Maps and IPF key", "palette") as si:
                s = load_settings().figure
                for ph in ("Ti (alpha)", "Ti (beta)"):
                    k = s.ipf.get(ph) or IpfKeySetting()
                    short = ph[4:-1]
                    with ui.row().classes("w-full no-wrap gap-2 items-end"):
                        ui.label({"alpha": "α", "beta": "β"}[short]).classes("w-4 pb-2")
                        ipf_w[ph] = (si.select(f"key_{short}", IPF_KEYS, k.key, "IPF key", "ipf_key"),
                                     si.select(f"dir_{short}", DIRECTIONS, k.direction, "reference direction", "ipf_dir"))
                cv_row = ui.row().classes("w-full no-wrap gap-2")
                with cv_row:
                    vs["cv"] = [si.num(f"cv_{c}", f"custom {c}", v, "ipf_dir", step=0.1)
                                for c, v in zip("xyz", (s.ipf["Ti (alpha)"].custom_vector or (0.0, 0.0, 1.0)))]

                def cv_vis(_=None):
                    cv_row.set_visibility(any(dd.value == "custom" for _k, dd in ipf_w.values()))
                for _k, dd in ipf_w.values():
                    dd.on_value_change(cv_vis)
                cv_vis()
                with ui.row().classes("w-full no-wrap gap-2"):
                    vs["cmin"] = si.num("cmin", "colour min", None, "clim", step=0.1)
                    vs["cmax"] = si.num("cmax", "colour max", None, "clim", step=0.1)
                with ui.row().classes("w-full no-wrap gap-2"):
                    vs["opacity"] = si.num("opacity", "opacity (0-1)", 1.0, step=0.1, min=0, max=1)
                    vs["lw"] = si.num("lw", "overlay line width [px]", 1.0, "line_width", step=0.5, min=0.5, max=6)
                ui.label("OIM axes: X = −A2, Y = −A1, Z = −A3 (EDAX setting 2): IPF-X = OIM IPF along A2, IPF-Y = "
                         "along A1, IPF-Z = OIM [001].").classes("text-xs ti-muted").mark("ipf_oim_axes")                     .tooltip("Checked against OIM Analysis on JU04_area4 (docs/orientation_check.md): app x (right) = "
                             "−A2, app y (down) = −A1, app z (into the screen) = −A3. A direction and its opposite have "
                             "the same IPF colour, so the maps match OIM's IPF maps along A2, A1 and A3 ([001]).")
                ui.label("Scale bar, colour bar and IPF key: the legend buttons in the map toolbar (next to 'Overlays') show them on the map; "
                         "drag them into place, the export puts them exactly there.").classes("text-xs ti-muted")

                async def apply_ipf():
                    await show()
                    fk = kind.value if kind.value in ("ipf", "phase", "ci", "iq", "fit", "kam", "grod", "gos", "gnd",
                                                      "boundaries", "burgers", *lay.PRIAS_KINDS) else None
                    if fk:
                        lp = layer_params()
                        step = await run_step("figure", {"kind": fk, "overlays": list(st["ovsel"]),
                                                         **({"ipf_keys": lp.ipf_keys} if lp.ipf_keys else {}),
                                                         **({"clim": lp.clim} if lp.clim else {})}, f"figure {fk}")
                        if step:
                            show_image(p.root / step.outputs["figure"].path, f"{fk} figure ({step.backend})",
                                       focus=False, step=step)
                    si.applied()

                def save_default():
                    stt = load_settings()
                    for ph, (kk, dd) in ipf_w.items():
                        stt.figure.ipf[ph] = IpfKeySetting(
                            key=kk.value, direction=dd.value,
                            custom_vector=tuple(float(c.value or 0) for c in vs["cv"]) if dd.value == "custom" else None,
                            key_in_figure=stt.figure.ipf.get(ph, IpfKeySetting()).key_in_figure)
                    ui.notify(f"IPF keys saved as default ({save_preferences(stt)})", type="positive")

                async def draw_key():
                    ph = "Ti (beta)" if kind.value == "parent_ipf" else "Ti (alpha)"
                    kk, dd = ipf_w[ph]
                    prm = {"kind": "ipf_key", "phases": [ph], "key": kk.value, "direction": dd.value}
                    if dd.value == "custom":
                        prm["custom_vector"] = [float(c.value or 0) for c in vs["cv"]]
                    step = await run_step("figure", prm, "IPF key")
                    if step:
                        show_image(p.root / step.outputs["figure"].path, f"IPF key {ph} ({step.backend})", step=step)
                with ui.row().classes("w-full gap-2"):
                    ui.button("Apply", icon="check", on_click=apply_ipf).mark("viewer_apply")
                    ui.button("IPF key", icon="donut_large", on_click=draw_key).props("outline").mark("viewer_ipfkey")
                    ui.button("Save as default", on_click=save_default).props("flat")

            # 3 grains and boundaries
            with Section(gs, "grains", "Grains and boundaries", "border_style") as sg:
                with ui.row().classes("w-full no-wrap gap-2"):
                    sg.num("hagb_deg", "HAGB [°]", g(lg, "grains", "hagb_deg", default=15.0), step=1, min=0)
                    sg.num("lagb_deg", "LAGB [°]", g(lg, "grains", "lagb_deg", default=2.0), step=0.5, min=0)
                with ui.row().classes("w-full no-wrap gap-2"):
                    sg.num("alpha", "gap bridging α", g(lg, "grains", "alpha", default=3.1), step=0.1, min=0)
                    sg.num("min_px", "min. grain [px]", g(lg, "grains", "min_px", default=1), "grain_min_px", step=1,
                           min=1)
                sg.num("burgers_tol_deg", "Burgers tolerance [°]", g(lg, "boundaries", "burgers_tol_deg", default=3.0),
                       step=0.5, min=0)

                async def apply_grains():
                    """On the α map: α grains of the loaded / cleaned map. On the prior β tab: prior-β grains of the
                    newest reconstruction (grains.calc on the parent map, fix 3.10). Stays on that tab and switches
                    the new HAGB overlay on there."""
                    t = tabkey()
                    prm = grains_params()
                    if t == "beta":
                        r_ = service.latest_step(p, ("recon.run",), map_id=st["map"])
                        if r_ is None:
                            ui.notify("Reconstruct the prior-β map first (section 'Prior-β reconstruction').",
                                      type="warning")
                            return
                        prm["source"] = r_.id
                    step = await run_step("grains.calc", prm, "prior-β grains" if t == "beta" else "grains")
                    if step:
                        sg.applied()
                        n = step.metrics.get("n_grains")
                        ovs = gs.get("overlays", st["map"])
                        ovs = dict(ovs) if isinstance(ovs, dict) else {"alpha": ovs or []}
                        ovs[t] = sorted(set(ovs.get(t) or (["beta_gb"] if t == "beta" else [])) | {"hagb"},
                                        key=lay.OVERLAYS.index)
                        gs.set("overlays", st["map"], ovs)
                        await select_map(st["map"], tab=t)
                        if n:
                            status.text = f"{int(n)} {'prior-β ' if t == 'beta' else ''}grains"
                ui.button("Apply", icon="check", on_click=apply_grains).mark("run_grains")

            def grains_params() -> dict:
                return {"grains": {"hagb_deg": sg.v("hagb_deg"), "lagb_deg": sg.v("lagb_deg"), "alpha": sg.v("alpha"),
                                   "min_px": int(sg.v("min_px") or 1)},
                        "boundaries": {"burgers_tol_deg": sg.v("burgers_tol_deg")}}

            # 4 KAM / GROD / GOS / GND
            with Section(gs, "kam", "KAM / GROD / GOS / GND", "blur_on") as sk:
                ui.label("Pick KAM, GROD, GOS or GND in the 'map' list of the map toolbar: each is computed the first "
                         "time it is shown (inside the α grains of 'Grains and boundaries'). These are their "
                         "parameters; Apply recomputes the map shown.").classes("text-xs ti-muted")
                ui.label("KAM").classes("text-sm font-semibold")
                with ui.row().classes("w-full no-wrap gap-2"):
                    sk.num("kam_order", "KAM order", g(lk, "kam_order", default=1), step=1, min=1, max=5)
                    sk.num("kam_threshold_deg", "KAM cutoff [°]", g(lk, "kam_threshold_deg", default=5.0), step=0.5,
                           min=0)
                ui.label("GND").classes("text-sm font-semibold")
                with ui.row().classes("w-full no-wrap gap-2"):
                    sk.select("gnd_stencil", ["1hop", "oneSided", "full"], g(lgnd, "gnd_stencil", default="1hop"),
                              "GND stencil")
                    sk.num("poisson", "Poisson's ratio", g(lgnd, "poisson", default=0.34), step=0.01, min=0, max=0.5)
                ui.label("GROD and GOS have no parameters (grain mean orientation of the α grains).") \
                    .classes("text-xs ti-muted")

                async def apply_kam():
                    k = kind.value if kind.value in ON_DEMAND else None
                    if k is None:
                        ui.notify("Show KAM, GROD, GOS or GND first (the 'map' list of the map toolbar); Apply "
                                  "recomputes the map shown with these parameters.", type="warning", multi_line=True)
                        return
                    if await compute_kind(k):
                        sk.applied()
                        await show()
                ui.button("Apply", icon="check", on_click=apply_kam).mark("run_map")

            # 5 reconstruction
            with Section(gs, "recon", "Prior-β reconstruction", "hub") as sr:
                meth = g(lr, "method", default="variantGraph")
                vg = meth == "variantGraph"
                sr.select("method", ["variantGraph", "graph"], meth, "method")
                with ui.row().classes("w-full no-wrap gap-2"):
                    sr.num("threshold", "threshold [°]", g(lr, "vgThreshold" if vg else "graphThreshold",
                                                            default=5.0 if vg else 2.0), "vgThreshold", step=0.5, min=0)
                    sr.num("tolerance", "tolerance [°]", g(lr, "vgTolerance" if vg else "graphTolerance", default=1.5),
                           "vgTolerance", step=0.1, min=0)
                sr.num("segAngle", "α grain angle [°]", g(lr, "segAngle", default=1.5), step=0.1, min=0)
                sr.select("orMode", {"limit": "refined OR if ≤ limit, else Burgers", "burgers": "ideal Burgers",
                                     "refined": "refined OR always"}, g(lr, "orMode", default="limit"),
                          "orientation relationship")
                sr.num("orMaxDev", "refined-OR limit [°]", g(lr, "orMaxDev", default=1.0), step=0.1, min=0) \
                    .bind_visibility_from(sr.fields["orMode"], "value", backward=lambda v: v == "limit")
                with ui.row().classes("w-full no-wrap gap-2"):
                    sr.select("mergeMode", ["single", "progressive"], g(lr, "mergeMode", default="single"), "merge")
                    sr.num("mergeThreshold", "merge threshold [°]", g(lr, "mergeThreshold", default=3.0), step=0.5,
                           min=0)
                sr.num("inclusionMaxSize", "inclusions < [px]", g(lr, "inclusionMaxSize", default=5), step=1, min=0)

                def rparams() -> dict:
                    d = {"method": sr.v("method"), "segAngle": sr.v("segAngle"), "orMode": sr.v("orMode"),
                         "orMaxDev": sr.v("orMaxDev"), "mergeMode": sr.v("mergeMode"),
                         "mergeThreshold": sr.v("mergeThreshold"), "inclusionMaxSize": int(sr.v("inclusionMaxSize") or 0)}
                    if sr.v("method") == "variantGraph":
                        d.update(vgThreshold=sr.v("threshold"), vgTolerance=sr.v("tolerance"))
                    else:
                        d.update(graphThreshold=sr.v("threshold"), graphTolerance=sr.v("tolerance"))
                    return d

                async def apply_recon():
                    step = await run_step("recon.run", {"params": rparams()}, "reconstruction")
                    if step:
                        sr.applied()
                        # the summary goes to Results, but the prior-beta MAP gets the focus (its own map tab)
                        show_block(lambda: recon_summary(p, step), f"Reconstruction {step.id} ({step.backend})",
                                   focus=False, step=step)
                        gs.set("layer", st["map"], "beta", "parent_ipf")
                        ovs = gs.get("overlays", st["map"])
                        if isinstance(ovs, dict):                  # a new reconstruction shows its boundaries
                            ovs.pop("beta", None)
                            gs.set("overlays", st["map"], ovs)
                        await select_map(st["map"], draw=True, tab="beta")
                        ui.notify("Prior-β map shown on the 'prior β' tab; the numbers are on the Results tab.",
                                  type="info")
                ui.button("Apply", icon="check", on_click=apply_recon).mark("run_recon")
                recon_figs = ui.column().classes("w-full gap-1")
                with recon_figs:
                    ui.separator().classes("my-2")
                    ui.label("Figures").classes("text-sm font-semibold")
                with ui.row().classes("w-full no-wrap gap-2 items-end").bind_visibility_from(recon_figs, "visible"):
                    rfk = sr.select("fig", RECON_FIG_KINDS, "parent_ipf", "figure", "recon_fig")

                    async def recon_fig():
                        step = await run_step("recon.figure", {"kind": rfk.value}, f"figure {rfk.value}")
                        if step:
                            show_image(p.root / step.outputs["figure"].path, f"{rfk.value} ({step.backend})",
                                       step=step)
                    ui.button("Draw", icon="brush", on_click=recon_fig).props("outline").mark("run_recon_fig")
                ui.label("Parameter sweep").classes("text-sm font-semibold mt-2")
                sr.text("sweep_thr", "thresholds [°]", "5, 8", "sweep_thr")
                sr.text("sweep_merge", "merge thresholds [°]", "0.5, 3, 6", "sweep_merge")

                async def sweep():
                    name = "vgThreshold" if sr.v("method") == "variantGraph" else "graphThreshold"
                    step = await run_step("recon.sweep", {"base": {**rparams(), "sweep": []},
                                                          "grid": {name: _floats(sr.v("sweep_thr")),
                                                                   "mergeThreshold": _floats(sr.v("sweep_merge"))}},
                                          "sweep")
                    if step:
                        show_block(lambda: sweep_table(p, step), f"Parameter sweep {step.id}", step=step)
                ui.button("Run sweep", icon="tune", on_click=sweep).props("outline").mark("run_sweep")

            # 6 grain size: works on the map tab it is started from (α map: α grains / laths / colonies; prior β tab:
            # prior-β grains of the newest reconstruction)
            with Section(gs, "grainsize", "Grain size", "straighten") as sz:
                gs_title = sz.title_label
                ga = ui.label("α grains = grains at the lath boundary angle below (an existing segmentation with "
                              "that angle is reused).").classes("text-xs ti-muted")
                gb_ = ui.label("Prior-β grains of the newest reconstruction (ECD; width / length across / along the "
                               "build direction when it is entered).").classes("text-xs ti-muted")
                tab_vis.extend([(ga, "alpha"), (gb_, "beta")])
                with ui.row().classes("w-full no-wrap gap-2"):
                    _gs_g = next((s_ for s_ in p.recipe().steps if s_.id == ls.get("grains")), None)
                    gh = sz.num("hagb", "lath boundary [°]",
                                g(_gs_g.params if _gs_g else {}, "grains", "hagb_deg", default=15.0), "gs_hagb",
                                step=1, min=0)
                    sz.num("min_px", "min. grain [px]", g(ls, "min_px", default=5), "gs_min_px", step=1, min=1)
                tab_vis.append((gh, "alpha"))
                sz.check("exclude_edge", "exclude edge grains", g(ls, "exclude_edge", default=True))
                arow = ui.row().classes("w-full no-wrap gap-2")
                with arow:
                    sz.num("lath_min_aspect", "lath aspect ≥", g(ls, "lath_min_aspect", default=2.0), step=0.5, min=1)
                    sz.num("colony_tol", "colony tol. [°]", g(ls, "colony", "burgers_tol_deg", default=3.0), step=0.5,
                           min=0)
                tab_vis.append((arow, "alpha"))
                bsp = sz.num("beta_line_spacing_um", "β line spacing [µm]", g(ls, "beta_line_spacing_um", default=1.0),
                             step=0.5, min=0.1)
                tab_vis.append((bsp, "beta"))

                async def apply_gs():
                    common = {"min_px": int(sz.v("min_px") or 1), "exclude_edge": sz.v("exclude_edge")}
                    if tabkey() == "beta":
                        step = await run_step("grainsize.run", {
                            **common, "target": "prior_beta", "beta_line_spacing_um": sz.v("beta_line_spacing_um")},
                            "prior-β grain size")
                    else:
                        hagb = float(sz.v("hagb") or 15.0)
                        from ..ops_ebsd import grains_target  # noqa: PLC0415

                        g_ = next((s_ for s_ in reversed(p.recipe().steps)
                                   if s_.op == "grains.calc" and not s_.stale and s_.params.get("map_id") == st["map"]
                                   and grains_target(p, s_) == "alpha"
                                   and abs(float(s_.params["grains"]["hagb_deg"]) - hagb) < 1e-9), None)
                        if g_ is None:
                            g_ = await run_step("grains.calc", {"grains": {"hagb_deg": hagb}}, "grains")
                            if not g_:
                                return
                        step = await run_step("grainsize.run", {
                            **common, "target": "alpha", "grains": g_.id, "lath_min_aspect": sz.v("lath_min_aspect"),
                            "colony": {"burgers_tol_deg": sz.v("colony_tol")}}, "α grain size")
                    if step:
                        sz.applied()
                        show_block(lambda: grainsize_summary(p, step), f"Grain size {step.id} ({step.backend})",
                                   step=step)
                        update_tab_parts(tabkey(), lay.available(p, st["map"]))
                ui.button("Apply", icon="check", on_click=apply_gs).mark("run_gs")
                gs_fig_row = ui.column().classes("w-full gap-1 mt-2")
                with gs_fig_row:
                    ui.label("Histograms (log-normal fit) and the colony map").classes("text-sm font-semibold")
                    gfk = sz.select("fig", GS_FIG_LABELS, "alpha_ecd", "figure", "gs_fig")
                    with ui.row().classes("w-full no-wrap gap-2 items-end"):
                        with ui.element("div").classes("grow min-w-0"):
                            bw = sz.num("bin_um", "bin width [µm] (empty = automatic)", None, "gs_bin", step=0.5,
                                        min=0).props("clearable")
                        bw.bind_visibility_from(gfk, "value", backward=lambda v: v != "colony_map")
                    with ui.row().classes("w-full no-wrap gap-2").bind_visibility_from(
                            gfk, "value", backward=lambda v: v != "colony_map"):
                        sz.num("min_um", "shown from [µm]", None, "gs_range", step=0.5, min=0) \
                            .props("clearable")
                        sz.num("max_um", "up to [µm]", None, "gs_range", step=1, min=0).props("clearable")
                    with ui.row().classes("w-full no-wrap gap-2 items-end"):
                        ui.space()

                        async def gs_fig():
                            prm = {"kind": gfk.value}
                            if gfk.value != "colony_map" and sz.v("bin_um"):
                                prm["bin_width_um"] = float(sz.v("bin_um"))
                            if gfk.value != "colony_map":
                                for k_ in ("min_um", "max_um"):
                                    if sz.v(k_) not in (None, ""):
                                        prm[k_] = float(sz.v(k_))
                            from ..ops_grainsize import grainsize_step_for  # noqa: PLC0415

                            sid = grainsize_step_for(p, st["map"], "prior_beta" if gfk.value in GS_BETA_KINDS
                                                     else "alpha")
                            s_ = p.recipe().step(sid) if sid else None
                            step = await run_step("grainsize.figure", prm, f"figure {gfk.value}",
                                                  s_.backend if s_ else None)
                            if step:
                                show_image(p.root / step.outputs["figure"].path,
                                           f"{GS_FIG_LABELS[gfk.value]} ({step.backend})", step=step)
                        ui.button("Draw", icon="brush", on_click=gs_fig).props("outline").mark("run_gs_fig")

                async def gs_oim():
                    f = await dialogs.open_files("OIM grain file", gs.get("last_folder") or "",
                                                 [("Text", "*.txt;*.csv")], multiple=False)
                    if not f:
                        return
                    s_ = service.latest_step(p, ("grainsize.run",), map_id=st["map"])
                    step = await run_step("grainsize.compare_oim", {"file": f[0]}, "OIM grain-size comparison",
                                          s_.backend if s_ else None)
                    if step:
                        show_image(p.root / step.outputs["comparison"].path, "Grain size vs OIM")
                oim_btn = ui.button("Compare with an OIM grain file…", icon="fact_check", on_click=gs_oim) \
                    .props("flat").tooltip(tip("gs_oim"))
                tab_vis.append((oim_btn, "alpha"))                   # compares α grains only

            # 6b texture (phase 6.7): ODF, pole figures, inverse pole figures in the specimen frame
            lt = lastp("texture.run")
            with Section(gs, "texture", "Texture", "radio_button_checked") as stx:
                ui.label("ODF of α or reconstructed prior-β orientations in the specimen frame (beam axis / "
                         "thickness / width, from the map sheet's position; BD from the sample sheet). Results: the "
                         "'Pole figures' and 'Inverse pole figures' tabs above the map.").classes("text-xs ti-muted")
                with ui.row().classes("w-full no-wrap gap-2"):
                    tph = stx.select("phase", {"alpha": "α", "beta": "prior β (reconstruction)"},
                                     g(lt, "phase", default="alpha"), "phase", "tex_phase")
                    tph.classes("min-w-0 flex-1")
                    stx.select("weighting", {"pixel": "per pixel (area)", "grain": "one point per grain"},
                               g(lt, "weighting", default="pixel"), "weighting", "tex_weighting") \
                        .classes("min-w-0 flex-1")
                with ui.row().classes("w-full no-wrap gap-2"):
                    treg = stx.select("region", REGION_LABELS, g(lt, "region", default="map"), "region", "tex_region")
                    troi = stx.select("roi_id", roi_options(), _in(g(lt, "roi_id"), roi_options()), "ROI", "tex_roi")
                troi.bind_visibility_from(treg, "value", backward=lambda v: v == "roi")
                with ui.row().classes("w-full no-wrap gap-2"):
                    stx.num("halfwidth_deg", "kernel halfwidth [°]", g(lt, "halfwidth_deg", default=5.0),
                            "tex_halfwidth", step=0.5, min=1, max=30)
                    stx.select("plot", {"density": "density (MRD)", "scatter": "scatter", "both": "both"},
                               g(lt, "plot", default="density"), "plot", "tex_plot")
                tpoles = stx.select("poles", list(PF_ALPHA), g(lt, "poles") or list(PF_ALPHA), "pole figures",
                                    "tex_poles", multiple=True).props("use-chips")
                tdirs = stx.select("ipf_dirs", IPF_DIR_OPTIONS, g(lt, "ipf_dirs", default=["BD", "beam axis", "width"]),
                                   "inverse pole figures along", "tex_ipf_dirs", multiple=True).props("use-chips")
                tcv = ui.row().classes("w-full no-wrap gap-2")
                with tcv:
                    tex_cv = [stx.num(f"cv_{c}", f"custom {c}", v, "tex_custom", step=0.1)
                              for c, v in zip("xyz", g(lt, "custom_vector") or (0.0, 0.0, 1.0))]
                tcv.bind_visibility_from(tdirs, "value", backward=lambda v: "custom" in (v or []))

                def tex_phase_changed(e):
                    opts = list(PF_ALPHA) if e.value == "alpha" else list(PF_BETA)
                    tpoles.options = opts
                    tpoles.value = [o for o in (tpoles.value or []) if o in opts] or opts
                    tpoles.update()
                tph.on_value_change(tex_phase_changed)
                if tph.value == "beta":
                    tpoles.options = list(PF_BETA)
                    tpoles.update()

                def tex_params() -> dict:
                    d = {"phase": stx.v("phase"), "weighting": stx.v("weighting"), "region": stx.v("region"),
                         "halfwidth_deg": float(stx.v("halfwidth_deg") or 5), "plot": stx.v("plot"),
                         "poles": list(stx.v("poles") or []) or None, "ipf_dirs": list(stx.v("ipf_dirs") or [])}
                    if d["region"] == "roi":
                        d["roi_id"] = stx.v("roi_id")
                    if "custom" in d["ipf_dirs"]:
                        d["custom_vector"] = [float(c.value or 0) for c in tex_cv]
                    return d

                async def apply_texture():
                    if stx.v("region") == "roi" and not stx.v("roi_id"):
                        ui.notify("Choose the ROI (draw one with an ROI tool of the 'tool' list first).",
                                  type="warning")
                        return
                    step = await run_step("texture.run", tex_params(), "texture")
                    if step:
                        stx.applied()
                        set_tab("pf")
                with ui.row().classes("w-full gap-2"):
                    ui.button("Apply", icon="check", on_click=apply_texture).mark("run_texture")
                    ui.button("Export ODF…", icon="ios_share", on_click=lambda: export_odf()).props("outline") \
                        .mark("export_odf").tooltip("The ODF of the newest texture result in MTEX's text format "
                                                    "(loadODF), with a JSON sidecar; listed under 'Earlier exports'")

            # 6c elastic properties (phase 6.7): Young's modulus from texture
            le = lastp("elastic.run")
            es_ = load_settings().elastic
            with Section(gs, "elastic", "Elastic properties", "compress") as sel_:
                ui.label("Young's modulus from the α (+ retained β) texture: single-crystal constants rotated into the "
                         "specimen frame and averaged (Voigt, Reuss, Hill), ± SD from a bootstrap over grains. "
                         "Constants: Settings, section 'Elastic constants'. Porosity is ignored. Result: the "
                         "'Elastic' tab.").classes("text-xs ti-muted")
                _pa = [k for k, v in es_.presets.items() if v.phase == "alpha"]
                _pb = [k for k, v in es_.presets.items() if v.phase == "beta"]
                sel_.select("preset_alpha", _pa, _in(g(le, "preset_alpha"), _pa) or _in(es_.default_alpha, _pa),
                            "α constants", "el_preset")
                sel_.select("preset_beta", _pb, _in(g(le, "preset_beta"), _pb) or _in(es_.default_beta, _pb),
                            "retained β constants", "el_preset")
                sel_.check("include_beta", "include retained β (by its area fraction)",
                           g(le, "include_beta", default=True), "el_include_beta")
                with ui.row().classes("w-full no-wrap gap-2"):
                    ereg = sel_.select("region", REGION_LABELS, g(le, "region", default="map"), "region", "tex_region")
                    eroi = sel_.select("roi_id", roi_options(), _in(g(le, "roi_id"), roi_options()), "ROI", "tex_roi")
                eroi.bind_visibility_from(ereg, "value", backward=lambda v: v == "roi")
                with ui.row().classes("w-full no-wrap gap-2"):
                    sel_.select("weighting", {"pixel": "per pixel (area)", "grain": "one point per grain"},
                                g(le, "weighting", default="pixel"), "weighting", "tex_weighting") \
                        .classes("min-w-0 flex-1")
                    sel_.num("bootstrap_runs", "bootstrap runs", g(le, "bootstrap_runs", default=200), "el_bootstrap",
                             step=50, min=0, max=5000).classes("min-w-0 flex-1")
                edirs = sel_.select("directions", IPF_DIR_OPTIONS, g(le, "directions",
                                                                     default=["BD", "beam axis", "width"]),
                                    "E along", "el_directions", multiple=True).props("use-chips")
                ecv = ui.row().classes("w-full no-wrap gap-2")
                with ecv:
                    el_cv = [sel_.num(f"cv_{c}", f"custom {c}", v, "tex_custom", step=0.1)
                             for c, v in zip("xyz", g(le, "custom_vector") or (1.0, 0.0, 0.0))]
                ecv.bind_visibility_from(edirs, "value", backward=lambda v: "custom" in (v or []))

                def el_params() -> dict:
                    d = {"preset_alpha": sel_.v("preset_alpha"), "preset_beta": sel_.v("preset_beta"),
                         "include_beta": bool(sel_.v("include_beta")), "region": sel_.v("region"),
                         "weighting": sel_.v("weighting"), "bootstrap_runs": int(sel_.v("bootstrap_runs") or 0),
                         "directions": list(sel_.v("directions") or [])}
                    if d["region"] == "roi":
                        d["roi_id"] = sel_.v("roi_id")
                    if "custom" in d["directions"]:
                        d["custom_vector"] = [float(c.value or 0) for c in el_cv]
                    return d

                async def apply_elastic():
                    step = await run_step("elastic.run", el_params(), "elastic properties")
                    if step:
                        sel_.applied()
                        set_tab("elastic")
                ui.button("Apply", icon="check", on_click=apply_elastic).mark("run_elastic")

            # 7 annotations
            with Section(gs, "annotations", "Annotations and ROIs", "edit"):
                ui.label("Pick a drawing tool in the 'tool' list of the map toolbar. 2-point tools: click start and end; polylines / crack / "
                         "ROI polygon: click points, double-click or Enter to finish; Esc cancels.") \
                    .classes("text-xs ti-muted")
                vs["ann_text"] = ui.input("text / name").classes("w-full").mark("ann_text")
                with ui.row().classes("w-full no-wrap gap-2"):
                    vs["ann_color"] = ui.color_input("colour", value="").classes("grow")
                    vs["ann_w"] = ui.number("width [pt]", value=1.5, step=0.5, min=0.25).classes("w-28")
                vs["ann_list"] = ui.column().classes("w-full gap-0").mark("ann_list")

            # 8 OIM image comparison
            with Section(gs, "oim", "Compare with an OIM image", "image_search"):
                ck = ui.select({"ipf": "IPF map", "ci": "CI map"}, value="ipf", label="kind").classes("w-full")

                async def oim():
                    f = await dialogs.open_files("OIM screenshot / export", gs.get("last_folder") or "",
                                                 [("Images", "*.png;*.jpg;*.tif;*.bmp")], multiple=False)
                    if f:
                        step = await run_step("compare.oim", {"image": f[0], "kind": ck.value}, "OIM comparison")
                        if step:
                            show_image(p.root / step.outputs["comparison"].path, "Comparison with OIM")
                ui.button("Choose image and compare…", icon="compare", on_click=oim).props("outline")

            # 9 any operation (advanced)
            with ui.expansion("Any operation (advanced)", icon="build", group="ebsd").classes("w-full ti-section") \
                    .mark("sec_ops"):
                ops_section(p, lambda: st["map"], run_step, show_image)

            # compare backends (History is the panel right of the map)
            with ui.expansion("Compare backends", icon="compare_arrows", group="ebsd").classes("w-full ti-section") \
                    .mark("sec_compare"):
                compare_section(p, lambda: st["map"], show_block, run_step, backend_status())

    # ---- phase 6.7: texture / elastic result tabs ---------------------------------------------------------------------
    def newest(op_: str):
        steps_ = [s_ for s_ in p.recipe().steps if s_.op == op_ and not s_.stale and s_.params.get("map_id") == st["map"]]
        return steps_[-1] if steps_ else None

    def _json_of(step, name="summary") -> dict:
        try:
            return json.loads((p.root / step.outputs[name].path).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return {}

    def texture_panel(t: str) -> None:
        """'Pole figures' / 'Inverse pole figures': the newest texture result of the map; 'Elastic': the newest
        elastic result and the per-sample table of E along the beam axis."""
        tex_box.clear()
        with tex_box:
            if t in ("pf", "ipf"):
                s_ = newest("texture.run")
                if s_ is None:
                    ui.label("No texture yet: section 'Texture' (left), Apply.").classes("text-sm ti-muted") \
                        .mark("texture_placeholder")
                    return
                d = _json_of(s_)
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{'Pole figures' if t == 'pf' else 'Inverse pole figures'} · "
                             f"{'α' if d.get('phase') == 'alpha' else 'prior β'} · {s_.id} ({s_.backend})") \
                        .classes("font-semibold grow")
                    ui.button("Export figure…", icon="ios_share",
                              on_click=lambda: export_result(s_, "pole figures" if t == "pf" else "inverse pole figures",
                                                             variant=t)).props("outline dense no-caps") \
                        .mark(f"export_{t}")
                    if t == "pf":
                        ui.button("Export ODF…", icon="data_object", on_click=lambda: export_odf()) \
                            .props("outline dense no-caps").mark("export_odf_tab")
                ui.image(str(p.root / s_.outputs[t].path)).classes("w-full max-w-5xl").props("fit=contain") \
                    .mark(f"{t}_image")
                for w_ in d.get("warnings", []):
                    ui.label("⚠ " + w_).classes("text-sm text-amber-800")
                fr = d.get("frame", {})
                rows = [("ODF", f"kernel density, halfwidth {d.get('halfwidth_deg')}°, {d.get('weighting')} weighting, "
                                f"{REGION_LABELS.get(d.get('region'), d.get('region'))}"),
                        ("max MRD", _fmt(d.get("max_mrd"))), ("texture index", _fmt(d.get("texture_index"))),
                        ("grains used", f"{d.get('n_grains')} ({d.get('n_grains_ecd_ge_5um')} with ECD ≥ 5 µm)"),
                        ("orientations", f"{d.get('n_orientations'):,}" + (f" (subsampled from {d.get('n_points'):,})"
                                                                         if d.get("subsampled_to") else "")),
                        ("maps", ", ".join(d.get("maps", []))), ("frame", fr.get("text", "")),
                        ("axes", " · ".join(f"{k}: {v}" for k, v in fr.get("labels", {}).items()))]
                if t == "pf":
                    rows += [(f"max MRD {k}", _fmt(v)) for k, v in d.get("pf_max_mrd", {}).items()]
                _table(rows, "texture_stats")
                return
            s_ = newest("elastic.run")
            if s_ is None:
                ui.label("No elastic result yet: section 'Elastic properties' (left), Apply.") \
                    .classes("text-sm ti-muted").mark("elastic_placeholder")
            else:
                d = _json_of(s_)
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"Young's modulus from texture · {s_.id} ({s_.backend})").classes("font-semibold grow")
                    ui.button("Export figure…", icon="ios_share", on_click=lambda: export_result(s_, "E(direction)")) \
                        .props("outline dense no-caps").mark("export_elastic")
                ui.image(str(p.root / s_.outputs["figure"].path)).classes("w-full max-w-4xl").props("fit=contain") \
                    .mark("elastic_image")
                cols = ["direction", "E Hill [GPa]", "± SD", "E Voigt", "E Reuss"]
                ui.table(columns=[{"name": c, "label": c, "field": c, "align": "left"} for c in cols],
                         rows=[{"direction": r["direction"], "E Hill [GPa]": f"{r['E_hill']:.1f}",
                                "± SD": f"{r['sd_hill']:.1f}" if "sd_hill" in r else "–",
                                "E Voigt": f"{r['E_voigt']:.1f}", "E Reuss": f"{r['E_reuss']:.1f}"}
                               for r in d.get("E_table", [])]).props("dense flat").mark("elastic_table")
                an = d.get("anisotropy", {}).get("hill", {})
                rows = [("anisotropy (Hill)", f"Emax / Emin = {_fmt(an.get('ratio'), 3)} ({_fmt(an.get('E_max'), 1)} / "
                                              f"{_fmt(an.get('E_min'), 1)} GPa)"),
                        ("grains", f"{d.get('n_grains')} ({d.get('n_grains_ecd_ge_5um')} with ECD ≥ 5 µm); "
                                   f"bootstrap {d.get('bootstrap_runs')} runs over grains")]
                rows += [(f"{k} ({100 * v['area_fraction']:.2f} % of the area)", f"{v['preset']} doi:{v['doi']}")
                         for k, v in d.get("phases", {}).items()]
                rows += [("note", n_) for n_ in d.get("notes", [])]
                _table(rows, "elastic_stats")
                with ui.expansion("Stiffness matrices (Voigt notation, specimen frame, GPa)").classes("w-full") \
                        .mark("elastic_matrices"):
                    for m_ in ("hill", "voigt", "reuss"):
                        M = d.get("matrices_GPa", {}).get(m_)
                        if M:
                            ui.label(m_.capitalize()).classes("text-sm font-semibold")
                            ui.html("<pre style='font-size:11px;margin:0'>" + "\n".join(
                                " ".join(f"{v:8.2f}" for v in row) for row in M) + "</pre>")
            sid = p.sample_of(st["map"])
            if sid:
                from ..ops_texture import sample_E_table  # noqa: PLC0415

                ui.label(f"Sample {sid}: E along the beam axis of every map (Hill, from each map's newest result)") \
                    .classes("font-semibold mt-2")
                rows_ = sample_E_table(p, sid)
                ui.table(columns=[{"name": c, "label": c, "field": c, "align": "left"}
                                  for c in ("map", "role", "E Hill [GPa]", "± SD", "grains")],
                         rows=[{"map": r["map"], "role": r["role"] or "–",
                                "E Hill [GPa]": _fmt(r["E_hill"], 1), "± SD": _fmt(r["sd"], 1),
                                "grains": r["n_grains"] if r["n_grains"] is not None else "–"} for r in rows_]) \
                    .props("dense flat").mark("sample_E_table")
                missing = [r["map"] for r in rows_ if r["E_hill"] is None]
                if missing:
                    ui.button(f"Compute for the {len(missing)} map(s) without a result", icon="playlist_play",
                              on_click=lambda: run_all_maps(missing)).props("outline dense no-caps") \
                        .mark("elastic_all_maps").tooltip("Runs 'Elastic properties' with the section's values on "
                                                          "each of these maps (each loads first if needed)")

    async def run_all_maps(maps: list[str]) -> None:
        prm = el_params()
        for m_ in maps:
            if not lay.available(p, m_)["loaded"]:
                if not await run_step("ebsd.load", {"map_id": m_}, f"loading {m_}", with_map=False):
                    return
            if not await run_step("elastic.run", {**prm, "map_id": m_}, f"elastic properties {m_}", with_map=False):
                return
        texture_panel("elastic")

    async def export_odf() -> None:
        s_ = newest("texture.run")
        if s_ is None:
            ui.notify("No texture result yet (section 'Texture', Apply).", type="warning")
            return
        folder = gs.get("export_folder") or str(_pictures())
        f_ = await dialogs.save_file("Export ODF (MTEX text)", f"{st['map']}_odf_{s_.id}.txt", folder,
                                     [("MTEX ODF text", "*.txt")])
        if not f_:
            return
        gs.set("export_folder", str(Path(f_).parent))
        try:
            await ng_run.io_bound(service.export, p, "texture.export_odf", {"texture": s_.id}, s_.backend, f_)
            ui.notify(f"ODF exported to {f_} (+ .json sidecar)", type="positive")
        except Exception as e:  # noqa: BLE001
            error(e, "ODF export failed")

    # ---- status badges (fix 5): which step each section compares its fields with ------------------------------
    def _step(sid):
        try:
            return p.recipe().step(sid) if sid else None
        except KeyError:
            return None

    sg.track(p, lambda: _step(lay.newest_grains(p.recipe().steps, st["map"], tabkey())),
             lambda s_: {**{k: s_.params.get("grains", {}).get(k) for k in ("hagb_deg", "lagb_deg", "alpha", "min_px")},
                         "burgers_tol_deg": s_.params.get("boundaries", {}).get("burgers_tol_deg")})

    def kam_step():
        k_ = kind.value if kind.value in ON_DEMAND else None
        steps_ = [s_ for s_ in p.recipe().steps if s_.op == "map.compute" and not s_.stale
                  and s_.params.get("map_id") == st["map"] and (k_ is None or s_.params.get("kind") == k_)]
        return steps_[-1] if steps_ else None

    def kam_rec(s_):
        k_ = s_.params.get("kind")
        keys = {"kam": ("kam_order", "kam_threshold_deg"), "gnd": ("gnd_stencil", "poisson")}.get(k_, ())
        return {k: s_.params.get(k) for k in keys}
    sk.track(p, kam_step, kam_rec)

    def recon_rec(s_):
        from ..backends.base import ReconParams  # noqa: PLC0415

        r_ = ReconParams(**(s_.params.get("params") or {}))
        vg_ = r_.method == "variantGraph"
        return {"method": r_.method, "threshold": r_.vgThreshold if vg_ else r_.graphThreshold,
                "tolerance": r_.vgTolerance if vg_ else r_.graphTolerance, "segAngle": r_.segAngle,
                "orMode": r_.orMode, "orMaxDev": r_.orMaxDev, "mergeMode": r_.mergeMode,
                "mergeThreshold": r_.mergeThreshold, "inclusionMaxSize": r_.inclusionMaxSize}
    sr.track(p, lambda: service.latest_step(p, ("recon.run",), map_id=st["map"]), recon_rec)

    def gs_step():
        tgt = "prior_beta" if tabkey() == "beta" else "alpha"
        steps_ = [s_ for s_ in p.recipe().steps if s_.op == "grainsize.run" and not s_.stale
                  and s_.params.get("map_id") == st["map"] and (s_.params.get("target") or "alpha") == tgt]
        return steps_[-1] if steps_ else None

    def gs_rec(s_):
        d_ = {"min_px": s_.params.get("min_px"), "exclude_edge": s_.params.get("exclude_edge")}
        if (s_.params.get("target") or "alpha") == "alpha":
            g_ = _step(s_.params.get("grains"))
            d_.update(lath_min_aspect=s_.params.get("lath_min_aspect"),
                      colony_tol=(s_.params.get("colony") or {}).get("burgers_tol_deg"),
                      hagb=(g_.params.get("grains") or {}).get("hagb_deg") if g_ else None)
        else:
            d_["beta_line_spacing_um"] = s_.params.get("beta_line_spacing_um")
        return {k: v for k, v in d_.items() if v is not None}
    sz.track(p, gs_step, gs_rec)
    stx.track(p, lambda: newest("texture.run"),
              lambda s_: {k: s_.params.get(k) for k in ("phase", "weighting", "region", "halfwidth_deg", "plot")})
    sel_.track(p, lambda: newest("elastic.run"), lambda s_: {
        "preset_alpha": s_.params.get("preset_alpha") or load_settings().elastic.default_alpha,
        "preset_beta": s_.params.get("preset_beta") or load_settings().elastic.default_beta,
        **{k: s_.params.get(k) for k in ("include_beta", "region", "weighting", "bootstrap_runs")}})
    all_secs = [sl, sc, *clean_secs.values(), sg, sk, sr, sz, stx, sel_]

    def sheet_info():
        from ..sample import missing_fields  # noqa: PLC0415

        try:
            sh = p.sample(st["map"])
            m = p.file(st["map"])
            bd = (f"BD {tuple(round(c, 3) for c in sh.build_direction.unit_vector)}" if sh.build_direction
                  else "build direction not entered")
            state = "OIM-cleaned" if sh.input_state == "oim_cleaned" else "raw"
            if m is not None and m.input_state is None:
                state += " (suggested from the file name)"
            from ..sample import indexing_label  # noqa: PLC0415

            lab = indexing_label(m)
            sheet_lab.text = (f"{sh.specimen_id or 'no sample'}: {state}, {bd}"
                              + (f", indexing: {lab}" if lab else ""))
            miss = missing_fields(m) if m is not None else []
            sheet_link.set_visibility(bool(miss))
        except (KeyError, ValueError):
            sheet_lab.text = "map sheet incomplete (Files tab)"
        load_info.text = frame_text()

    def frame_text() -> str:
        """Load section: EDAX setting, the crystal frame (file vs backend) and the OIM axes of the newest load."""
        s_ = service.latest_step(p, ("ebsd.load",), map_id=st["map"])
        if s_ is None or "frame" not in s_.outputs:
            return ("Loads the .ang with EDAX setting 2 (x right, y down; app x = −A2, y = −A1, z = −A3 of OIM)."
                    if s_ is None else "")
        try:
            fr = json.loads((p.root / s_.outputs["frame"].path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        cf = fr.get("crystal_frame")
        cf = ", ".join(sorted(set(cf.values()))) if isinstance(cf, dict) else str(cf)
        t_ = (f"Loaded {badges_when(s_)} ({s_.backend}), EDAX setting {fr.get('setting')}: app x = −A2, y = −A1, "
              f"z = −A3 of OIM. Crystal frame {cf}; the file says '{fr.get('cartesian_alignment') or '?'}'.")
        if fr.get("warning"):
            t_ += " ⚠ " + fr["warning"]
        return t_

    def badges_when(s_) -> str:
        from ..badges import when  # noqa: PLC0415

        return when(s_)

    def frame_notice(step) -> None:
        """Warn when a loaded .ang's crystal frame is not the X||a both backends assume (fix 5)."""
        ref = step.outputs.get("frame")
        if ref is None:
            return
        try:
            w_ = json.loads((p.root / ref.path).read_text(encoding="utf-8")).get("warning")
        except (OSError, ValueError):
            return
        if w_:
            ui.notify(f"{st['map']}: {w_}", type="warning", multi_line=True, timeout=0, close_button="OK")

    def refresh_badges() -> None:
        for sec_ in all_secs:
            sec_._caption()
        clean_summaries()
        sheet_info()

    async def on_map_change(e):
        await select_map(e.value)
    msel.on_value_change(on_map_change)
    async def compute_kind(k: str) -> bool:
        """KAM / GROD / GOS / GND of the shown map with the section's parameters (fix 3.9); α grains first if the
        map has none yet."""
        if lay.available(p, st["map"])["grains_alpha"] is None:
            ui.notify(f"{k.upper()} is computed inside α grains: running 'Grains and boundaries' with its values "
                      "first.", type="info")
            if not await run_step("grains.calc", grains_params(), "grains", channel=st["pch"]):
                return False
        prm = {"kind": k, "kam_order": int(sk.v("kam_order") or 1), "kam_threshold_deg": sk.v("kam_threshold_deg"),
               "gnd_stencil": sk.v("gnd_stencil"), "poisson": sk.v("poisson")}
        step = await run_step("map.compute", prm, k.upper(), channel=st["pch"] if st["loading_ui"] else None)
        if not step:
            return False
        av = lay.available(p, st["map"])
        kind.options = kind_labels(list(kind.options), av["layers"])
        kind.update()
        return True

    async def on_kind(e):
        if not e.value or st["selecting"]:
            return
        if e.value in ON_DEMAND and e.value not in lay.available(p, st["map"])["layers"]:
            loading(st["map"], f"Computing {e.value.upper()} for")
            progress.set(st["pch"], f"computing {e.value.upper()}")
            if not await compute_kind(e.value):
                st["loading_ui"] = False
                await show_or_back()
                return
        await show()

    async def show_or_back():
        kind.set_value("ipf" if "ipf" in kind.options else next(iter(kind.options), None))
    kind.on_value_change(on_kind)
    tool.on_value_change(lambda e: viewer["v"] and viewer["v"].set(tool=e.value))
    vs["opacity"].on_value_change(lambda e: viewer["v"] and viewer["v"].set(layer_opacity=float(e.value or 1)))
    vs["lw"].on_value_change(lambda e: show_overlays())

    async def on_mtab(e):
        if st.get("switching"):
            return
        if e.value in PANEL_TABS:
            set_tab(e.value)
        elif e.value != st["mtab"] or not map_col.visible:
            await select_map(st["map"], tab=e.value)
    mtabs.on_value_change(on_mtab)
    with hist_box:
        hooks["history"] = history_section(p, run_step, current_map=lambda: st["map"], current_tab=tabkey,
                                           on_changed=lambda: select_map(st["map"]))

    async def undo_key(e):
        if e.action.keydown and e.modifiers.ctrl and e.key.name in ("z", "y"):
            lab = service.undo(p) if e.key.name == "z" else service.redo(p)
            ui.notify((("undone: " if e.key.name == "z" else "redone: ") + lab) if lab else "nothing to undo / redo",
                      type="positive" if lab else "warning")
            if lab:
                ui.navigate.reload()
    ui.keyboard(on_key=undo_key, ignore=["input", "select", "button", "textarea"])

    async def do_export():
        if st["layer"] is None:
            ui.notify("Load the map first.", type="warning")
            return
        items = [a for a in load_annotations(p, st["map"]) if a.type in ROI_TYPES]
        rois = {a.id: f"ROI {a.text or a.id}" for a in items}
        L = st["layer"].layout
        geometry = {"extent": list(L.extent), "step": [L.dx, L.dy], "hex": bool(L.hex),
                    "roi_boxes": {a.id: roi_box(a) for a in items}}
        sty = {k: v for k, v in ov_style().items() if k in st["ovsel"]}
        await export_dialog(p, gs, kind="map", map_id=st["map"], layer=layer_params().model_dump(mode="json"),
                            overlays=list(st["ovsel"]), view=st["view"], rois=rois, run_step=run_step,
                            geometry=geometry, legends=legend_items(), overlay_style=sty,
                            view_state={"overlays": bool(st["ovsel"]), "annotations": True})
    do_export = stable(do_export)

    sheet_info()
    ui.timer(0.05, lambda: select_map(st["map"]), once=True)


def _pictures() -> Path:
    from ..settings import pictures_dir  # noqa: PLC0415

    return pictures_dir()


def _reveal_dir(d: Path) -> None:
    import os  # noqa: PLC0415

    try:
        os.startfile(str(d))              # noqa: S606
    except Exception as e:  # noqa: BLE001
        error(e)


def _floats(text: str) -> list[float]:
    return [float(t) for t in str(text).replace(";", ",").split(",") if t.strip()]


def _table(rows: list[tuple[str, str]], marker: str):
    ui.table(columns=[{"name": "k", "label": "", "field": "k", "align": "left"},
                      {"name": "v", "label": "", "field": "v", "align": "left"}],
             rows=[{"k": k, "v": str(v)} for k, v in rows]).props("dense flat wrap-cells").classes("w-full").mark(marker)


def recon_summary(p: Project, step) -> None:
    s = json.loads((p.root / step.outputs["summary"].path).read_text(encoding="utf-8"))
    dev = s.get("or_dev_deg")
    or_txt = (f"{s['or_used']} (refined OR deviates {dev:.2f}° from Burgers; {s['or_rule']})"
              if dev is not None else f"{s['or_used']} ({s['or_rule']})")
    rows = [("backend", s["backend"]), ("OR", or_txt),
            ("prior β grains ≥ ECD min (all fragments)", f"{s['n_grains_min_ecd']} ({s['n_grains_all']})"),
            ("reconstructed α area", f"{100 * s['rec_frac']:.1f} %"),
            ("mean fit / fit > bad", f"{s['mean_fit_deg']:.2f}° / {100 * s['bad_fit_frac']:.1f} %"),
            ("fit quantiles 10/25/50/75/90 %", ", ".join(f"{v:.2f}" for v in s["fit_quantiles"])),
            ("area-weighted ECD", f"{s['ecd_aw_um']:.1f} µm"),
            ("bridged steps / split noise", f"{s['bridged_pct']:.1f} % / {s['split_pct']:.1f} %")]
    gs_ = s["grain_shape"]
    if gs_.get("aspect_along_across"):
        rows.append(("caliper along / across BD (area-w., edge grains excluded)",
                     f"{gs_['along_bd_um']['area_weighted_mean']:.1f} / {gs_['across_bd_um']['area_weighted_mean']:.1f} µm, "
                     f"aspect {gs_['aspect_along_across']['area_weighted_mean']:.2f} (n = {gs_['n_used']})"))
    rows += [("warning", w) for w in gs_.get("warnings", [])]
    _table(rows, "recon_summary")
    cols = ["config"] + s["sweep_columns"]
    ui.label("merge sweep").classes("font-semibold mt-2")
    ui.table(columns=[{"name": c, "label": c, "field": c} for c in cols],
             rows=[{"config": f"{m} {t:g}°", **{c: (f"{v:.1f}" if isinstance(v, float) else v)
                                                for c, v in zip(cols[1:], r)}}
                   for (m, t), r in zip(s["sweep_configs"], s["sweep"])]).props("dense flat")


def sweep_table(p: Project, step) -> None:
    t = json.loads((p.root / step.outputs["table"].path).read_text(encoding="utf-8"))
    cols = ["run", "params", "grains", "all", "recFrac %", "fit p50", "fit p90", "ECDaw", "bridged %", "split %"]
    ui.table(columns=[{"name": c, "label": c, "field": c} for c in cols], rows=[{
        "run": r["run"], "params": ", ".join(f"{k} {v}" for k, v in r["params"].items()),
        "grains": r["n_grains_min_ecd"], "all": r["n_grains_all"], "recFrac %": f"{100 * r['rec_frac']:.1f}",
        "fit p50": f"{r['fit_quantiles'][2]:.2f}", "fit p90": f"{r['fit_quantiles'][4]:.2f}",
        "ECDaw": f"{r['ecd_aw_um']:.1f}", "bridged %": f"{r['bridged_pct']:.1f}", "split %": f"{r['split_pct']:.1f}"}
        for r in t["runs"]]).props("dense flat").mark("sweep_table")
    with ui.grid(columns=3).classes("w-full"):
        for k in sorted(o for o in step.outputs if o.startswith("thumb_")):
            ui.image(str(p.root / step.outputs[k].path))


def _in(v, options):
    """v if it is one of the options (a select's value must be), else None."""
    return v if v is not None and v in options else None


def _fmt(v, nd=2) -> str:
    return "–" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def grainsize_summary(p: Project, step) -> None:
    s = json.loads((p.root / step.outputs["summary"].path).read_text(encoding="utf-8"))

    def stat(d, aw=True):
        if not d:
            return "–"
        t = f"mean {_fmt(d['mean'])}, median {_fmt(d['median'])} µm (n = {d['n']})"
        if aw and d.get("area_weighted"):
            t += f"; area-weighted {_fmt(d['area_weighted']['mean'])} µm"
        ln = d.get("lognormal") or {}
        if ln.get("sigma"):
            t += f"; log-normal median {_fmt(ln['median'])}, σ {_fmt(ln['sigma'])}"
        return t
    a, c, b, rb = s.get("alpha"), s.get("colony"), s.get("prior_beta"), s["retained_beta"]
    rows = [("backend / rules", f"{s['backend']}; {s['rules']}")]
    if a:
        rows += [("lath partition", f"grains.calc {s['grains']}: HAGB {s['lath_partition']['hagb_deg']:g}°"),
                 ("alpha grain ECD", stat(a["ecd"])),
                 ("lath thickness, intercept ⊥ long axis", stat(a["thickness_intercept"], False)),
                 ("lath thickness, minor axis 2b", stat(a["thickness_minor_axis"], False)),
                 ("colony ECD", stat(c["ecd"]) + f" ({c['n_alpha_grains_merged']} alpha grains merged)")]
    if b:
        rows.append(("reconstruction", f"{b['recon']} ({b['recon_backend']})"))
        rows.append(("prior-β ECD", stat(b["ecd"])))
        for k, lab in (("across_bd", "prior-β width (across BD)"), ("along_bd", "prior-β length (along BD)")):
            if k in b:
                rows.append((lab, f"E112 mean intercept {_fmt(b[k]['mean_e112_um'], 1)} µm "
                                  f"({b[k]['n_complete']} complete + {b[k]['n_truncated']} edge chords)"))
    if "loaded" in rb:
        rows.append(("retained β (indexed fraction)", f"loaded {100 * rb['loaded']['fraction']:.3f} %, "
                                                      f"analysed map {100 * rb['source_map']['fraction']:.3f} %"))
    elif "reconstruction" in rb:
        rows.append(("retained β after reconstruction", f"{100 * rb['reconstruction']['fraction_of_kept']:.3f} % "
                                                        "of the kept pixels"))
    rows += [("warning", w) for w in s["warnings"]]
    _table(rows, "gs_summary")
    for k, r in step.outputs.items():
        if r.kind in ("csv", "xlsx"):
            ui.label(f"{k}: {p.root / r.path}").classes("text-xs ti-muted break-all")
