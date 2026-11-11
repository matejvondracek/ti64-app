"""Stitch tab. Left: the images (all images of a sample, a folder or picked files), the detector, the STITCH SETS of
the selection (same magnification + pixel size + overlapping stage footprints; different magnifications are never
stitched together) and a checklist of the images of the chosen set (pick a subset), then the options (info strip,
blending, seam, band width, search radius, min. score). Right: Arrange (thumbnails at their stage positions, or in a
row without stage data: drag them roughly into place, zoom, lock a tile, select with click / Shift-click / Shift-drag;
once tiles were moved, the one 'Stitch' button searches only within the search radius around these positions and the
manual layout is saved in the recipe. 'Do not use stage positions' (under 'Stitch sets', e.g. for a faulty stage):
the Arrange view starts from a grid and Stitch places the tiles by image matching of all pairs or from the Arrange
positions only (fix 3.17)),
Result (the mosaic zoomable down to full resolution in the tiled viewer, optional tile borders coloured by match
score and seams, click a tile to nudge / exclude it, pairs with their residual misalignment; fix 3.16) and Overlaps
(difference image of one overlap: ghosting shows as double edges).
Everything runs through service.run("sem.stitch", ...) (recorded in the recipe); Export uses the shared export
dialog (sem.export). Single images are opened on the View tab."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from nicegui import run as ng_run
from nicegui import ui
from nicegui.element import Element

from .. import service
from ..modules import stitching
from . import dialogs, tiles
from .export_dialog import export_dialog
from .mosaic_tiles import border_overlay, open_mosaic, seam_overlay
from .shell import check_interrupted, error, header
from .state import GuiState, stable
from .tips import tip
from .viewer import MapViewer

STITCH_STATE: dict = {"msg": "", "frac": 0.0, "busy": False}


class ArrangeView(Element, component="arrange_view.js"):
    """Thumbnails at their positions (image px), drag / zoom / select / lock (arrange_view.js)."""

    def __init__(self) -> None:
        super().__init__()
        self._props.update(tiles=[], fit_token=0)

    def set_tiles(self, t: list[dict]) -> None:
        self._props["tiles"] = t
        self.update()

    def fit(self) -> None:
        self._props["fit_token"] = self._props.get("fit_token", 0) + 1
        self.update()


def score_colour(score: float | None, placed_by: str, min_score: float) -> str:
    """Tile border colour: green = strong match, orange = acceptable, red = weak / placed by stage only."""
    if placed_by.startswith(("stage", "manual")) or score is None or (isinstance(score, float) and np.isnan(score)):
        return "#d62728"
    if "weak" in placed_by:
        return "#d62728"
    if score >= max(0.6, min_score):
        return "#2ca02c"
    if score >= min_score:
        return "#ff7f0e"
    return "#d62728"


def tile_svg(side: dict, scale: float, selected: str | None = None, min_score: float = 0.3) -> str:
    """SVG rectangles (preview pixel coordinates) of every tile, coloured by its best match score."""
    out = []
    for t in side["tiles"]:
        x, y = t["x_px"] / scale, t["y_px"] / scale
        w, h = t["width_px"] / scale, t["height_px"] / scale
        c = score_colour(t["best_score"], t["placed_by"], min_score)
        sw = 4 if t["name"] == selected else 2
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="none" stroke="{c}" '
                   f'stroke-width="{sw}" />')
        label = f'{t["image_number"]} ({t["best_score"] if t["best_score"] is not None else "stage"})'
        out.append(f'<text x="{x + 6:.1f}" y="{y + 18:.1f}" fill="{c}" font-size="14" '
                   f'font-family="sans-serif">{label}</text>')
    return "".join(out)


def tile_key(t: stitching.TileInfo) -> str:
    """Key of a tile in manual layouts / exclusions: its image number (shared by BSE and SE), else the file stem."""
    return str(t.image_number) if t.image_number is not None else t.name


def stage_layout(tl: list[stitching.TileInfo], use_stage: bool = True) -> dict[str, list[float]]:
    """Positions (x, y px, top-left corners) from the stage (default JSM-IT500HR mapping), or in a grid without stage
    data / with 'Do not use stage positions'."""
    lay = stitching.layout_preview(tl, use_stage=use_stage)
    return {tile_key(t): [L["x"], L["y"]] for t, L in zip(tl, lay["tiles"])}


def single_column(layout: dict, tl: list[stitching.TileInfo]) -> bool:
    xs = [layout[tile_key(t)][0] for t in tl if tile_key(t) in layout]
    return bool(xs) and (max(xs) - min(xs)) <= 0.05 * tl[0].width


def predicted_single_column(tiles_: list[stitching.TileInfo]) -> bool:
    return single_column(stage_layout(tiles_), tiles_) if tiles_ else False


STRIP_OPTS = {"none": "none (for analysis)", "keep": "keep the original data bar", "generate": "generate a new one"}
BLEND_OPTS = {"seam": "narrow band around each seam", "feather": "across the whole overlap",
              "none": "no blending (raw pixels)"}
SEAM_OPTS = {"midline": "midline of the overlap", "min_difference": "minimum-difference path"}


def stitch_page() -> None:
    p = header("Stitch")
    if p is None:
        ui.navigate.to("/")
        return
    gs = GuiState(p)
    gs.set("tab", "/stitch")
    check_interrupted(p)
    images = [m for m in p.files() if m.kind == "image"]
    samples = sorted({m.sample_id for m in images if m.sample_id})
    src_opts = {**{f"sample:{s}": f"All images of sample {s} ({sum(m.sample_id == s for m in images)})"
                   for s in samples},
                **({"unassigned": f"Unassigned images ({sum(m.sample_id is None for m in images)})"}
                   if any(m.sample_id is None for m in images) else {}),
                "folder": "A folder…", "files": "Files chosen from disk…"}
    sel_file = gs.get("selected_file")
    sel_img = next((m for m in images if m.id == sel_file), None)
    saved = gs.get("stitch") or {}
    default_src = saved.get("source") if saved.get("source") in src_opts else (
        f"sample:{sel_img.sample_id}" if sel_img and sel_img.sample_id else None)
    if default_src not in src_opts:
        default_src = next(iter(src_opts))
    src: dict = {"folder": saved.get("folder"), "files": saved.get("files"), "tiles": [], "sample": None}
    st: dict = {"sets": [], "set": saved.get("set"), "checked": set(), "layout": {}, "locked": set(),
                "manual": False, "last": None, "side": {}}
    edits: dict = {"exclude": set(), "nudge": {}}

    with ui.row().classes("w-full no-wrap gap-0 items-stretch").style("height: calc(100vh - 56px)"):
        left = ui.column().classes("h-full shrink-0 bg-white border-r gap-0").style("width: 380px").mark("stitch_left")
        right = ui.column().classes("h-full grow gap-0 min-w-0")
    with left:
        with ui.scroll_area().classes("w-full grow"):
            with ui.column().classes("w-full gap-3 p-3"):
                ui.label("Stitch SEM images").classes("text-lg font-semibold")
                source = ui.select(src_opts, value=default_src, label="Images").classes("w-full").mark("st_source") \
                    .tooltip("All images of one sample, a folder, or files chosen from disk.")
                with ui.row().classes("w-full no-wrap gap-2 items-end") as folder_row:
                    folder = ui.input("folder", value=src["folder"] or "").classes("grow").mark("st_folder")
                    ui.button(icon="folder_open", on_click=lambda: pick()).props("outline round dense") \
                        .tooltip("Browse…").mark("st_browse")
                    ui.button(icon="search", on_click=lambda: scan()).props("outline round dense") \
                        .tooltip("Read the images again").mark("st_scan")
                det = ui.select({"BSE": "BSE", "SE": "SE", "both": "both"}, value=saved.get("detector", "BSE"),
                                label="detector").classes("w-full").tooltip(tip("st_detector")).mark("st_detector")
                counts = ui.column().classes("w-full gap-1")
                ui.label("Stitch sets").classes("font-semibold").tooltip(tip("st_sets"))
                no_stage = ui.checkbox("Do not use stage positions", value=not saved.get("use_stage", True)) \
                    .props("dense").classes("text-sm").tooltip(tip("st_no_stage")).mark("st_no_stage")
                sets_box = ui.column().classes("w-full gap-1").mark("st_sets")
                stage_box = ui.column().classes("w-full gap-1").mark("st_stage_check")
                ui.label("Images of the set").classes("font-semibold") \
                    .tooltip("Untick images to leave them out. In the Arrange view: click, Shift-click or "
                             "Shift-drag a box to select.")
                with ui.row().classes("w-full gap-2"):
                    ui.button("all", on_click=lambda: check_all(True)).props("flat dense no-caps").mark("st_all")
                    ui.button("none", on_click=lambda: check_all(False)).props("flat dense no-caps").mark("st_none")
                checklist = ui.column().classes("w-full gap-0").mark("st_checklist")
                se_bse = ui.checkbox("stitch BSE, apply the layout to SE", value=saved.get("se_bse", False)) \
                    .tooltip(tip("st_se_layout")).mark("st_se_layout")
                strip = ui.select(dict(STRIP_OPTS), value=saved.get("strip", "none"), label="info strip") \
                    .classes("w-full").tooltip(tip("st_strip")).mark("st_strip")
                # greyed option with the reason as a hover tooltip (Quasar option slot)
                strip.add_slot("option", r'''
                    <q-item v-bind="props.itemProps" :disable="props.opt.disable">
                      <q-item-section><q-item-label>{{ props.opt.label }}</q-item-label></q-item-section>
                      <q-tooltip v-if="props.opt.reason" max-width="320px">{{ props.opt.reason }}</q-tooltip>
                    </q-item>''')
                blend = ui.select(BLEND_OPTS, value=saved.get("blend", "seam"), label="blending").classes("w-full") \
                    .tooltip(tip("st_blend")).mark("st_blend")
                with ui.row().classes("w-full no-wrap gap-2"):
                    seam = ui.select(SEAM_OPTS, value=saved.get("seam", "midline"), label="seam").classes("grow") \
                        .tooltip(tip("st_seam")).mark("st_seam")
                    band = ui.number("band width [px]", value=saved.get("band", 32), min=0, step=8, format="%d") \
                        .classes("w-32").tooltip(tip("st_band")).mark("st_band")
                with ui.row().classes("w-full no-wrap gap-2"):
                    radius = ui.number("search radius [% of tile]", value=saved.get("radius", 10), min=1, max=50,
                                       step=1, format="%g").classes("grow").mark("st_radius") \
                        .tooltip("How far a match may lie from the position on the 'Arrange' tab (after you moved "
                                 "tiles there), in % of the tile size.")
                    min_score = ui.number("min. match score", value=saved.get("min_score", 0.3), step=0.05,
                                          min=0, max=1).classes("w-32").tooltip(tip("st_min_score"))
        with ui.column().classes("w-full p-3 gap-2 border-t"):
            prog = ui.linear_progress(value=0, show_value=False).props("rounded").classes("w-full").mark("st_progress")
            prog_lab = ui.label("").classes("text-xs ti-muted")
            with ui.row().classes("w-full no-wrap gap-2"):
                ui.button("Stitch", icon="view_module", on_click=lambda: do_stitch()).classes("grow") \
                    .mark("st_run").tooltip("Register and stitch the ticked images: from the positions on the "
                                            "'Arrange' tab (within the search radius) or, if you did not move any "
                                            "tile, from the stage positions (by image matching only when "
                                            "'Do not use stage positions' is ticked)")
                exp_btn = ui.button("Export", icon="ios_share", on_click=lambda: do_export()).props("outline") \
                    .classes("grow").mark("st_export")
    with right:
        tabs = ui.tabs().classes("w-full bg-white border-b").props("dense align=left no-caps")
        with tabs:
            ui.tab("arrange", label="Arrange", icon="open_with").mark("st_tab_arrange")
            ui.tab("result", label="Result", icon="view_module").mark("st_tab_result")
            ui.tab("overlaps", label="Overlaps", icon="difference").mark("st_tab_overlaps")
        with ui.tab_panels(tabs, value="arrange").classes("w-full grow").props("keep-alive") as panels:
            with ui.tab_panel("arrange").classes("p-0 h-full"):
                with ui.column().classes("w-full h-full gap-0"):
                    with ui.row().classes("w-full items-center gap-2 px-3 py-2 bg-white border-b no-wrap"):
                        reset_btn = ui.button("Stage positions", icon="restart_alt", on_click=lambda: reset_layout()) \
                            .props("outline dense no-caps").mark("st_reset") \
                            .tooltip("Put every tile back at its stage position (in a grid without stage data or with "
                                     "'Do not use stage positions')")
                        ui.button("Lock", icon="lock", on_click=lambda: lock(True)).props("outline dense no-caps") \
                            .mark("st_lock").tooltip("Selected tiles keep their position when you press 'Stitch'")
                        ui.button("Unlock", icon="lock_open", on_click=lambda: lock(False)) \
                            .props("outline dense no-caps").mark("st_unlock")
                        ui.button(icon="fit_screen", on_click=lambda: arr.fit()).props("flat round dense") \
                            .tooltip("Fit all tiles (double-click the background)").mark("st_fit")
                        arr_lab = ui.label("").classes("text-xs ti-muted truncate grow").mark("st_arrange_info")
                    arr = ArrangeView().classes("w-full grow").style("min-height: 300px").mark("st_arrange")
            with ui.tab_panel("result").classes("p-3 h-full overflow-auto"):
                result = ui.column().classes("w-full gap-3").mark("st_result")
                with result:
                    ui.label("Press the 'Stitch' button (next to 'Export') to see the mosaic here.").classes("ti-muted")
            with ui.tab_panel("overlaps").classes("p-3"):
                overlaps = ui.column().classes("w-full gap-3").mark("st_overlaps")
                with overlaps:
                    ui.label("Stitch first; then pick an overlap to see the difference of the two tiles.") \
                        .classes("ti-muted")

    # ---- state ---------------------------------------------------------------------------------------------
    def src_key() -> str:
        v = source.value
        return v if v.startswith(("sample:", "unassigned")) else f"{v}:{src.get('folder') or ''}:{len(src.get('files') or [])}"

    def remember():
        gs.set("stitch", {"source": source.value, "folder": src["folder"], "files": src["files"], "detector": det.value,
                          "se_bse": se_bse.value, "strip": strip.value, "blend": blend.value, "seam": seam.value,
                          "band": band.value, "radius": radius.value, "min_score": min_score.value, "set": st["set"],
                          "use_stage": not no_stage.value})
        gs.set("stitch_arrange", src_key(), {"set": st["set"], "checked": sorted(st["checked"]),
                                             "layout": st["layout"], "locked": sorted(st["locked"]),
                                             "manual": st["manual"]})
    for w in (se_bse, blend, seam, band, radius, min_score):
        w.on_value_change(lambda _: remember())
    strip.on_value_change(lambda _: remember())

    def det_class() -> str:
        return "SE" if det.value == "SE" else "BSE"

    def set_tiles() -> list[stitching.TileInfo]:
        s = next((x for x in st["sets"] if x.id == st["set"]), None)
        if s is None:
            return []
        paths = set(s.paths)
        return [t for t in src["tiles"] if t.path in paths]

    def checked_tiles() -> list[stitching.TileInfo]:
        return [t for t in set_tiles() if tile_key(t) in st["checked"]]

    # ---- source --------------------------------------------------------------------------------------------
    def upd_source():
        v = source.value
        folder_row.set_visibility(v in ("folder", "files"))
        if v.startswith("sample:") or v == "unassigned":
            sid = v[7:] if v.startswith("sample:") else None
            src.update(files=[m.file for m in images if m.sample_id == sid], folder=None, sample=sid)
        scan()
        remember()
    source.on_value_change(lambda _: upd_source())

    async def pick():
        if source.value == "files":
            f = await dialogs.open_files("Images to stitch", folder.value or gs.get("last_folder") or "",
                                         [("SEM images", "*.bmp;*.tif;*.tiff")])
            if f:
                src.update(files=f, folder=None)
                folder.value = f"{Path(f[0]).parent}  [{len(f)} files]"
                scan()
        else:
            d = await dialogs.pick_folder("Folder with the SEM images", folder.value or gs.get("last_folder") or "")
            if d:
                src.update(folder=d, files=None)
                folder.value = d
                if source.value != "folder":
                    source.value = "folder"
                scan()
        remember()
    pick = stable(pick)

    def scan():
        if source.value == "folder":
            src.update(folder=folder.value.split("  [")[0].strip() or None, files=None, sample=None)
        if not src["folder"] and not src["files"]:
            src["tiles"] = []
            refresh_sets()
            return
        try:
            src["tiles"] = stitching.scan(src["folder"] if not src["files"] else None, src["files"])
        except Exception as e:  # noqa: BLE001
            error(e, "Could not read the images")
            src["tiles"] = []
        c = stitching.detector_counts(src["tiles"])
        det.options = {"BSE": f"BSE ({c.get('BSE', 0)})", "SE": f"SE ({c.get('SE', 0)})",
                       "both": f"both ({c.get('BSE', 0)} + {c.get('SE', 0)}, separate mosaics)"}
        det.update()
        counts.clear()
        with counts:
            names = stitching.detector_detail(src["tiles"])
            ui.label(f"{len(src['tiles'])} images: " + ", ".join(f"{k} {v}" for k, v in names.items())) \
                .classes("text-xs ti-muted").mark("st_counts")
        refresh_sets()

    def refresh_sets():
        tl = [t for t in src["tiles"] if t.detector_class == det_class()]
        st["sets"] = stitching.stitch_sets(tl, use_stage=not no_stage.value)
        stitchable = [s for s in st["sets"] if s.n > 1]
        mem = gs.get("stitch_arrange", src_key()) or {}
        if st["set"] not in {s.id for s in stitchable}:
            st["set"] = mem.get("set") if mem.get("set") in {s.id for s in stitchable} else (
                max(stitchable, key=lambda s: s.n).id if stitchable else None)
        sets_box.clear()
        with sets_box:
            if not tl:
                ui.label(f"No {det_class()} images in this selection.").classes("text-sm ti-muted")
            if stitchable:
                r = ui.radio({s.id: s.label for s in stitchable}, value=st["set"]).props("dense").classes("text-sm") \
                    .mark("st_set_radio")
                r.on_value_change(lambda e: choose_set(e.value))
            singles = [s for s in st["sets"] if s.n == 1]
            if singles:
                ui.label("Not stitchable (single images; open them on the View tab): " +
                         ", ".join(f"{s.image_numbers[0] or s.names[0]} "
                                   f"({f'{s.magnification:g}×' if s.magnification else 'no .txt'})" for s in singles)) \
                    .classes("text-xs ti-muted").mark("st_singles")
        load_arrangement(mem if mem.get("set") == st["set"] else {})

    det.on_value_change(lambda _: (refresh_sets(), remember()))

    def no_stage_change(_=None):
        """'Do not use stage positions' (fix 3.17): sets are no longer split by stage footprints; the Arrange view
        starts from a grid unless the tiles were arranged by hand."""
        reset_btn.text = "Grid" if no_stage.value else "Stage positions"
        refresh_sets()
        if not st["manual"]:
            st["layout"] = stage_layout(set_tiles(), not no_stage.value)
            refresh_arrange(fit=True)
        remember()
    no_stage.on_value_change(no_stage_change)
    reset_btn.text = "Grid" if no_stage.value else "Stage positions"

    def choose_set(set_id: str):
        st["set"] = set_id
        load_arrangement({})
        remember()

    def load_arrangement(mem: dict):
        tl = set_tiles()
        keys = {tile_key(t) for t in tl}
        st["checked"] = set(mem.get("checked") or keys) & keys
        st["layout"] = {k: v for k, v in (mem.get("layout") or {}).items() if k in keys}
        if set(st["layout"]) != keys:
            st["layout"] = stage_layout(tl, not no_stage.value)
        st["locked"] = set(mem.get("locked") or []) & keys
        st["manual"] = bool(mem.get("manual"))
        refresh_checklist()
        show_stage_check(tl)
        refresh_arrange(fit=True)

    def show_stage_check(tl: list[stitching.TileInfo]):
        """Possible repeats of the chosen set (same stage position and magnification). A stage axis without spread
        is no longer reported (fix 3.17); 'Do not use stage positions' is the way around a faulty stage."""
        st["stage_check"] = sa = stitching.stage_analysis(tl) if len(tl) > 1 else {"repeats": []}
        stage_box.clear()
        if not sa["repeats"]:
            return
        with stage_box, ui.row().classes("w-full no-wrap gap-2 p-2 rounded bg-amber-50 border border-amber-300"):
            ui.icon("warning", size="20px").classes("text-amber-700 shrink-0")
            with ui.column().classes("gap-1 min-w-0"):
                if sa["repeats"]:
                    ui.label("Possible repeats (same stage position and magnification): " +
                             "; ".join(" = ".join(g) for g in sa["repeats"]) +
                             ". 'Stitch' tells you on the 'Result' tab whether they show the same area; untick a "
                             "repeat under 'Images of the set' to leave it out.").classes("text-xs") \
                        .mark("st_repeats")

    def refresh_checklist():
        checklist.clear()
        with checklist:
            for t in set_tiles():
                k = tile_key(t)
                cb = ui.checkbox(f"{t.image_number or ''} {t.name}".strip(), value=k in st["checked"]) \
                    .props("dense").classes("text-sm").mark(f"st_chk_{k}")
                cb.on_value_change(lambda e, k=k: toggle_check(k, e.value))

    def toggle_check(k: str, on: bool):
        (st["checked"].add if on else st["checked"].discard)(k)
        refresh_arrange()
        remember()

    def check_all(on: bool):
        st["checked"] = {tile_key(t) for t in set_tiles()} if on else set()
        refresh_checklist()
        refresh_arrange()
        remember()

    def refresh_arrange(fit: bool = False):
        tl = set_tiles()
        arr.set_tiles([{"key": tile_key(t), "name": t.name, "label": str(t.image_number or t.name),
                        "url": tiles.thumb_url(t.path, t.strip_px), "x": st["layout"][tile_key(t)][0],
                        "y": st["layout"][tile_key(t)][1], "w": t.width, "h": t.height,
                        "locked": tile_key(t) in st["locked"], "selected": tile_key(t) in st["checked"]}
                       for t in tl if tile_key(t) in st["layout"]])
        if fit:
            arr.fit()
        n = len(checked_tiles())
        arr_lab.text = (f"{n} of {len(tl)} images selected · " +
                        ("manual arrangement" if st["manual"] else
                         "stage positions not used: tiles in a grid; Stitch places them by image matching, or drag "
                         "them roughly into place" if no_stage.value else "stage positions") +
                        (f" · locked: {', '.join(sorted(st['locked']))}" if st["locked"] else ""))
        update_strip_option()

    strip_flag: dict = {"disable": False, "reason": ""}
    _orig_update_options = strip._update_options

    def _strip_options():                      # NiceGUI rebuilds the options on every update: re-apply the flag
        _orig_update_options()
        for o in strip._props.get("options", []):
            if strip._values[o["value"]] == "keep":
                o.update(strip_flag)
    strip._update_options = _strip_options

    def update_strip_option():
        tl = checked_tiles()
        single = single_column(st["layout"], tl) if tl else False
        strip_flag.update(disable=not single, reason="" if single else (
            "Only possible for a single column of images (the original data bar of the bottom image must fit under "
            "the mosaic); the ticked images are not arranged in one column."))
        if not single and strip.value == "keep":
            strip.value = "generate"
        strip.update()

    def on_moved(e):
        for mv in e.args.get("moves", []):
            st["layout"][mv["key"]] = [float(mv["x"]), float(mv["y"])]
        st["manual"] = True
        refresh_arrange()
        remember()

    def on_select(e):
        keys = {tile_key(t) for t in set_tiles()}
        st["checked"] = set(e.args.get("keys", [])) & keys
        refresh_checklist()
        refresh_arrange()
        remember()
    arr.on("moved", on_moved)
    arr.on("select", on_select)

    def reset_layout():
        st["layout"] = stage_layout(set_tiles(), not no_stage.value)
        st["manual"] = False
        refresh_arrange(fit=True)
        remember()

    def lock(on: bool):
        sel = st["checked"]
        if not sel:
            ui.notify("Select tiles in the Arrange view first (click / Shift-click / Shift-drag).", type="warning")
            return
        if on:
            st["locked"] |= sel
        else:
            st["locked"] -= sel
        refresh_arrange()
        remember()

    # ---- stitching -----------------------------------------------------------------------------------------
    def tick():
        prog.value = STITCH_STATE["frac"]
        if STITCH_STATE["busy"]:
            prog_lab.text = STITCH_STATE["msg"]
    ui.timer(0.3, tick)

    def params(manual: bool) -> dict:
        tl = checked_tiles()
        if len(tl) < 2:
            raise ValueError("Tick at least two images under 'Images of the set' (choose the set under "
                             "'Stitch sets' first).")
        files = [t.path for t in tl]
        if det.value == "both" or (det.value == "SE" and se_bse.value):
            nums = {t.image_number for t in tl}
            other = "SE" if det_class() == "BSE" else "BSE"
            files += [t.path for t in src["tiles"] if t.detector_class == other and t.image_number in nums]
        pr = {"files": files, "detector": det.value, "se_layout": "bse" if se_bse.value else "own",
              "info_strip": strip.value, "blend": blend.value, "seam": seam.value,
              "blend_band_px": int(band.value or 0), "min_score": float(min_score.value or 0.3),
              "exclude": sorted(edits["exclude"]), "nudge": {k: list(v) for k, v in edits["nudge"].items()},
              "refine_radius": float(radius.value or 10) / 100.0, "use_stage": not no_stage.value}
        if manual:                       # tiles moved on the Arrange tab: their positions replace the stage
            pr.update(manual_layout={tile_key(t): st["layout"][tile_key(t)] for t in tl},
                      locked=sorted(st["locked"] & {tile_key(t) for t in tl}))
        if src.get("sample"):
            pr["sample_id"] = src["sample"]
        return pr

    async def do_stitch():
        """One button (fix 14 of the 6.6 user test): registration incl. the refinement within the search radius,
        from the Arrange positions once tiles were moved there, else from the stage positions."""
        if STITCH_STATE["busy"]:
            return
        try:
            prm = params(bool(st["manual"]))
        except Exception as e:  # noqa: BLE001
            error(e, "Cannot stitch yet")
            return
        STITCH_STATE.update(busy=True, frac=0.0, msg="starting")
        stitching.PROGRESS["hook"] = lambda m, f: STITCH_STATE.update(msg=m, frac=f)
        try:
            step = await ng_run.io_bound(service.run, p, "sem.stitch", prm)
            ui.notify(f"stitched ({step.id}, {step.metrics.get('seconds')} s); the mosaic is on the Result tab",
                      type="positive")
            show_result(step)
        except Exception as e:  # noqa: BLE001
            error(e, "Stitching failed")
        finally:
            stitching.PROGRESS["hook"] = None
            STITCH_STATE.update(busy=False, frac=1.0)
            prog_lab.text = ""
    do_stitch = stable(do_stitch)

    def show_result(step):
        st["last"] = step
        result.clear()
        panels.value = "result"
        with result:
            for d in ("BSE", "SE"):
                if f"sidecar_{d}" not in step.outputs:
                    continue
                side = json.loads((p.root / step.outputs[f"sidecar_{d}"].path).read_text(encoding="utf-8"))
                st["side"][d] = side
                w, h = side["mosaic_px"]
                mis = side.get("misalignment_px") or {}
                with ui.column().classes("ti-card p-3 w-full gap-2"):
                    ui.label(f"{step.sample_id + ' · ' if step.sample_id else ''}{d}: {side['n_tiles']} tiles → "
                             f"{w} × {h} px ({(side['pixel_size_um'] or 0) * 1000:.2f} nm/px); fit residual max "
                             f"{side['residual_px']['max']} px; misalignment after placement max "
                             f"{mis.get('max')} px; placed without a match: {side['stage_only_tiles'] or 'none'}; "
                             f"blending {side['blend']}" + (f" ({side['render']['seam']} seam, "
                                                           f"{side['render']['blend_band_px']} px band)"
                                                           if side.get('render', {}).get('blend') == 'seam' else "")) \
                        .classes("text-sm").mark(f"st_summary_{d}")
                    stage_notes = [q for q in side.get("notes", []) if not q.startswith("MP_BEAM_SHIFT")]
                    if stage_notes:                       # stage not used, repeats (confirmed or not), unmatched
                        with ui.column().classes("w-full gap-0 p-2 rounded bg-amber-50 border border-amber-300") \
                                .mark(f"st_notes_{d}"):
                            for q in stage_notes:
                                ui.label("• " + q[:1].upper() + q[1:]).classes("text-xs")
                    mosaic_view(step, d, side)
                    with ui.row().classes("gap-2"):
                        ui.button(f"Export {d}…", icon="ios_share", on_click=lambda d=d: do_export(d)) \
                            .props("outline").mark(f"st_export_{d}")
                        ui.button("Open on the View tab", icon="image_search",
                                  on_click=lambda d=d: (gs.set("view_source", f"mosaic:{step.id}:{d}"),
                                                        gs.set("view_file", None), ui.navigate.to("/view"))) \
                            .props("flat").mark(f"st_view_{d}")
                    ui.label("Pairs (click a row to see the difference image of that overlap)").classes("font-semibold")
                    cols = ["a", "b", "score", "used", "residual_px", "misalignment_px", "diff_mean", "reason"]
                    rows = [{"id": k, **{c: ("" if q.get(c) is None else str(q.get(c))) for c in cols}}
                            for k, q in enumerate(side["pairs"])]
                    tbl = ui.table(columns=[{"name": c, "label": c.replace("_", " "), "field": c, "align": "left"}
                                            for c in cols], rows=rows, row_key="id", pagination=10) \
                        .props("dense flat").classes("w-full").mark(f"st_pairs_{d}")
                    tbl.on("rowClick", lambda e, d=d, side=side: show_overlap(d, side, e.args[1]["a"], e.args[1]["b"]))
        show_overlap_picker()

    def mosaic_view(step, d: str, side: dict):
        """The mosaic in the tiled viewer: pan / zoom down to full resolution (pyramid read lazily), tile borders
        coloured by match score and the seams as optional overlays, click a tile to nudge / exclude it (fix 3.16)."""
        ps = side.get("pixel_size_um") or 1.0
        key = f"mos_{step.id}_{d}"
        try:
            url, ext = open_mosaic(key, p.root / step.outputs[f"mosaic_{d}"].path, ps, int(side["mosaic_px"][1]))
        except Exception as e:  # noqa: BLE001
            error(e, "Could not open the mosaic")
            return
        ms = side["params"]["min_score"]
        ui.add_css(".ti64-label span { font: 600 12px sans-serif; white-space: nowrap; "
                   "text-shadow: 0 0 3px #fff, 0 0 2px #fff; }")
        opts = gs.get("stitch_result_view", default=None) or {"borders": True, "seams": False}
        seams_npz = (p.root / step.outputs[f"seams_{d}"].path) if f"seams_{d}" in step.outputs else None
        cache: dict = {}
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            cb_b = ui.checkbox("tile borders", value=opts.get("borders", True)).props("dense") \
                .mark(f"st_borders_{d}").tooltip("Outline of every tile at its final position, coloured by its best "
                                                 "match score: green strong, orange acceptable, red weak or placed "
                                                 "without a match. Labels: image number (score).")
            cb_s = ui.checkbox("seams", value=opts.get("seams", False)).props("dense").mark(f"st_seams_{d}") \
                .tooltip("Where one tile ends and the next begins in the mosaic (blending happens in a narrow band "
                         "around these lines).")
            seam_lab = ui.label("").classes("text-xs ti-muted truncate grow")
        v = MapViewer(ps, ext).classes("w-full border").style("height: 62vh; min-height: 360px") \
            .mark(f"st_result_{d}")
        st.setdefault("viewers", {})[d] = v

        def shapes(sel: str | None = None) -> list[dict]:
            out = []
            if cb_b.value:
                for t in side["tiles"]:
                    c = score_colour(t["best_score"], t["placed_by"], ms)
                    out.append({"id": f"lab_{t['name']}", "type": "text", "width_pt": 1, "color": c,
                                "xy": [[(t["x_px"] + 0.02 * t["width_px"]) * ps, (t["y_px"] + 0.04 * t["height_px"]) * ps]],
                                "text": f"{t['image_number']} · " + (f"{t['best_score']:.2f}" if isinstance(
                                    t["best_score"], (int, float)) else "no match")})
            t = next((t for t in side["tiles"] if t["name"] == sel), None)
            if t is not None:
                out.append({"id": "sel", "type": "roi_rect", "width_pt": 2, "color": "#1e88e5", "text": t["name"],
                            "xy": [[t["x_px"] * ps, t["y_px"] * ps],
                                   [(t["x_px"] + t["width_px"]) * ps, (t["y_px"] + t["height_px"]) * ps]]})
            return out

        def overlays():
            ov = []
            if cb_b.value:
                cache.setdefault("b", border_overlay(key, side, lambda t: score_colour(t["best_score"], t["placed_by"],
                                                                                       ms)))
                ov.append({"url": cache["b"], "opacity": 1})
            if cb_s.value:
                if "s" not in cache:
                    try:
                        cache["s"] = seam_overlay(key, side, seams_npz)
                    except Exception as e:  # noqa: BLE001
                        error(e, "Could not draw the seams")
                        cache["s"] = None
                if cache["s"]:
                    url, why, n = cache["s"]
                    ov.append({"url": url, "opacity": 1})
                    seam_lab.text = (f"seams: {why}" if n else "no seams (feather blending: the whole overlap is "
                                     "blended)")
            else:
                seam_lab.text = ""
            gs.set("stitch_result_view", {"borders": bool(cb_b.value), "seams": bool(cb_s.value)})
            v.set(overlays=ov, shapes=shapes(st.get("sel_tile", {}).get(d)))

        box = ui.column().classes("w-full gap-2")

        def probe(e):
            x, y = e.args["xy"][0]
            hit = [t for t in side["tiles"] if t["x_px"] * ps <= x < (t["x_px"] + t["width_px"]) * ps
                   and t["y_px"] * ps <= y < (t["y_px"] + t["height_px"]) * ps]
            if not hit:
                return
            t = hit[-1]
            st.setdefault("sel_tile", {})[d] = t["name"]
            v.set(shapes=shapes(t["name"]))
            tile_panel(t, box)
        v.on("probe", probe)
        v.on("cursor", throttle=0.2, handler=lambda e: seam_lab.set_text(
            f"x {e.args['x'] / ps:.0f} px, y {e.args['y'] / ps:.0f} px · click a tile to nudge or exclude it"))
        v.set(layer_url=url, tool="probe", legends=[], legend_data={})
        cb_b.on_value_change(lambda _: overlays())
        cb_s.on_value_change(lambda _: overlays())
        overlays()

    def tile_panel(t, box):
        box.clear()
        key = str(t["image_number"]) if t["image_number"] not in (None, -1) else t["name"]
        nx, ny = edits["nudge"].get(key, (0.0, 0.0))
        with box:
            with ui.column().classes("ti-card p-2 w-full gap-1").mark("st_tile_panel"):
                ui.label(f"Tile {t['name']}").classes("font-semibold text-sm")
                ui.label(f"x {t['x_px']} px, y {t['y_px']} px, score {t['best_score']}, {t['placed_by']}") \
                    .classes("text-xs ti-muted")
                with ui.row().classes("w-full no-wrap gap-2"):
                    dx = ui.number("nudge x [px]", value=nx, step=1).classes("w-32").mark("st_nudge_dx")
                    dy = ui.number("nudge y [px]", value=ny, step=1).classes("w-32").mark("st_nudge_dy")
                    ex = ui.checkbox("exclude this tile", value=key in edits["exclude"]).mark("st_exclude")

                async def apply():
                    if dx.value or dy.value:
                        edits["nudge"][key] = (float(dx.value or 0), float(dy.value or 0))
                    else:
                        edits["nudge"].pop(key, None)
                    (edits["exclude"].add if ex.value else edits["exclude"].discard)(key)
                    await do_stitch()
                ui.button("Re-stitch", icon="refresh", on_click=apply).mark("st_restitch")

    def show_overlap_picker():
        overlaps.clear()
        with overlaps:
            pairs = [(d, q) for d, side in st["side"].items() for q in side["pairs"] if q.get("used")]
            if not pairs:
                ui.label("No used overlaps in the last mosaic.").classes("ti-muted")
                return
            opts = {f"{d}|{q['a']}|{q['b']}": f"{d}: {q['a']} ↔ {q['b']} (misalignment {q.get('misalignment_px')} px)"
                    for d, q in pairs}
            first = next(iter(opts))
            ui.select(opts, value=first, label="overlap",
                      on_change=lambda e: show_overlap(*e.value.split("|"))).classes("w-full max-w-3xl") \
                .mark("st_overlap_select")
            st["ov_box"] = ui.column().classes("w-full gap-2")
        show_overlap(*first.split("|"), switch=False)

    def show_overlap(d: str, side_or_a, a=None, b=None, switch: bool = True):
        if isinstance(side_or_a, dict):              # from the pairs table
            side = side_or_a
        else:
            side, a, b = st["side"][d], side_or_a, a
        box = st.get("ov_box")
        if box is None:
            return
        try:
            img, info = stitching.overlap_difference(side, a, b)
        except Exception as e:  # noqa: BLE001
            error(e, "Could not draw the overlap")
            return
        from PIL import Image  # noqa: PLC0415

        f = p.cache_dir / "stitch_overlaps" / f"diff_{st['last'].id}_{d}_{a}_{b}.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(f)
        box.clear()
        with box:
            ui.label(f"|{a} − {b}| in their overlap ({info['size_px'][0]} × {info['size_px'][1]} px): residual "
                     f"misalignment {info.get('misalignment_px')} px (dx, dy {info.get('misalignment_dxdy_px')}), "
                     f"mean difference {info.get('diff_mean')} vs pixel noise ≈ {info.get('noise')}; white = "
                     f"difference ≥ {info['scale_max']}").classes("text-sm").mark("st_overlap_info")
            ui.label("A well registered overlap looks like uniform noise; ghosting shows as double edges or bright "
                     "outlines of features.").classes("text-xs ti-muted")
            ui.image(str(f)).classes("max-w-4xl border").props("fit=contain").mark("st_overlap_img")
        if switch:
            panels.value = "overlaps"

    async def do_export(d: str | None = None):
        step = st["last"]
        if step is None:
            ui.notify("Stitch first.", type="warning")
            return
        d = d or next((x for x in ("BSE", "SE") if f"mosaic_{x}" in step.outputs), "BSE")

        async def run_step(op, prm, label, backend=None, with_map=False):
            n = ui.notification(f"{label}…", spinner=True, timeout=None)
            try:
                return await ng_run.io_bound(service.run, p, op, prm, backend)
            except Exception as e:  # noqa: BLE001
                error(e, f"{label} failed")
            finally:
                n.dismiss()
        side = st["side"].get(d) or {}
        from ..legends import default_item  # noqa: PLC0415

        lg = gs.get("view_legend", default=None) or default_item("scale_bar").model_dump()
        has_strip = bool((side.get("strip") or {}).get("height_px"))
        await export_dialog(p, gs, kind="mosaic", run_step=run_step, stitch_step=step.id, detector=d,
                            sample_id=step.sample_id, legends=[lg] if side.get("pixel_size_um") else [],
                            geometry={"size_px": side.get("mosaic_px") or [1, 1],
                                      "pixel_size_um": side.get("pixel_size_um")},
                            view_state={"info_strip": has_strip, "overlays": False, "annotations": False})
    do_export = stable(do_export)

    upd_source()
    last = service.latest_step(p, ("sem.stitch",))
    if last:
        show_result(last)
        panels.value = "arrange"
    exp_btn.set_enabled(True)
