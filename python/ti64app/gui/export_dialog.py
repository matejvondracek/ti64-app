"""Export dialog, shared by the EBSD tab (maps: export.map), the Stitch tab (mosaics: sem.export) and the View tab
(single images / mosaics: sem.export).

Format PNG / TIFF / SVG / PDF; resolution native (1 px per measurement / image pixel), 2x / 4x / 8x (maps), a size
typed in pixels (width OR height, the aspect ratio is locked) or a width in mm at a chosen DPI. The dialog shows the
final size in pixels, the physical size in um and an estimated file size. Legends (scale bar, colour bar, IPF key)
are NOT chosen here: the export shows exactly the ones placed on the map / image view (what you see is what you get).
Location by the Windows save dialog; the first default folder is the user's Pictures folder, then the last folder
used in the project. Exports are NOT recipe steps (fix 3.4): service.export runs the op unrecorded, keeps the file in
exports/<id>/, copies it to the chosen file and adds an entry to the export log ("Export again" repeats one with the
same settings). What is included (overlays, annotations, the microscope info bar) starts from what the view shows
(view_state, fix 3.5: what you see is what you get); format and resolution are remembered (gui_state.json)."""
from __future__ import annotations

import math
from pathlib import Path

from nicegui import run as ng_run
from nicegui import ui

from .. import exportlog, service
from ..project import Project
from ..settings import pictures_dir
from . import dialogs
from .shell import error, notify_actions
from .state import GuiState

EXT = {"png": ".png", "tiff": ".tif", "svg": ".svg", "pdf": ".pdf", "bigtiff": ".tif"}
TYPES = {"png": ("PNG image", "*.png"), "tiff": ("TIFF image", "*.tif;*.tiff"), "svg": ("SVG vector", "*.svg"),
         "pdf": ("PDF document", "*.pdf"), "bigtiff": ("BigTIFF image (tiled)", "*.tif;*.tiff")}
FRACTIONS = {"1/2": 2, "1/4": 4, "1/8": 8}          # images / mosaics: export size as a fraction of the original
DEFAULTS = {"format": "png", "res": "native", "width_mm": 120.0, "dpi": 600, "px_w": None, "region": "map"}
INFO_BAR_LABEL = ("Add the microscope info bar under the image (detector, kV, WD, magnification, scale bar, image "
                  "number, date)")
INFO_BAR_TIP = ("The JEOL-style data bar below the image: detector, accelerating voltage, working distance, "
                "magnification, a scale bar, the image number(s) and the date, as recorded in the .txt files. With "
                "it, the scale bar placed on the view is left out (only one scale bar).")
LEGEND_NAMES = {"scale_bar": "scale bar", "colorbar": "colour bar / colour legend", "ipf_key": "IPF colour key"}
HEX_NOTE = ("Hexagonal map: native and 2× draw each measurement as a rectangle (brick layout: one step wide, rows "
            "shifted by half a step, like hexRaster in EBSD_part2.m; exact, one pixel per measurement at native). "
            "4× and larger draw every measurement as its true regular hexagon (square pixels of step / scale). Figures "
            "by width use hexagons when they have ≥ 4 pixels per measurement.")


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def export_size(res: str, region_um: tuple[float, float], step: tuple[float, float], hexgrid: bool,
                width_mm: float, dpi: float, px_w: float | None, native_px: tuple[int, int] | None = None) -> dict:
    """Final pixel size, pixel size (um) and physical size of an export (what the ops will write, to +-1 px)."""
    w_um, h_um = region_um
    dx, dy = step
    if res in FRACTIONS and native_px is not None:          # 1/2, 1/4, 1/8 of an image / mosaic (box average)
        k = FRACTIONS[res]
        W, H = -(-native_px[0] // k), -(-native_px[1] // k)          # PIL reduce rounds up
        px = py = w_um / max(native_px[0], 1) * k
        return {"W": W, "H": H, "px_um": px, "py_um": py, "w_um": w_um, "h_um": h_um}
    if res == "native" or res in ("2", "4", "8"):
        k = 1 if res == "native" else int(res)
        if native_px is not None and k == 1:
            W, H = native_px
            px = py = w_um / max(W, 1)
        elif hexgrid and k >= 4:
            px = py = dx / k
            W, H = math.ceil(w_um / px - 1e-6), math.ceil(h_um / py - 1e-6)
        else:
            px, py = dx / k, dy / k
            W, H = math.ceil(w_um / px - 1e-6), math.ceil(h_um / py - 1e-6)
    elif res == "px":
        W = max(1, int(round(px_w or 1)))
        H = max(1, int(round(W * h_um / max(w_um, 1e-9))))
        px = py = w_um / W
    else:
        W = max(1, int(round(width_mm / 25.4 * dpi)))
        H = max(1, int(round(W * h_um / max(w_um, 1e-9))))
        px = py = w_um / W
    return {"W": W, "H": H, "px_um": px, "py_um": py, "w_um": w_um, "h_um": h_um}


def estimate_bytes(fmt: str, W: int, H: int, kind: str) -> float:
    """Rough file size: maps compress to ~0.45 byte / px (PNG / zlib TIFF of RGB), greyscale SEM images ~0.8."""
    per = {"map": 0.45, "mosaic": 0.8, "image": 0.8}[kind]
    if fmt in ("svg", "pdf"):
        per *= 1.35 if fmt == "svg" else 1.05                      # embedded PNG (base64 in SVG) + vector lines
    return W * H * per + 4000


async def export_dialog(p: Project, gs: GuiState, kind: str, run_step, map_id: str | None = None,
                        layer: dict | None = None, overlays: list[str] | None = None, view: dict | None = None,
                        rois: dict | None = None, stitch_step=None, detector: str = "BSE",
                        sample_id: str | None = None, image: str | None = None, geometry: dict | None = None,
                        legends: list[dict] | None = None, overlay_style: dict | None = None,
                        view_state: dict | None = None) -> None:
    """geometry: {extent: [x0, x1, y0, y1] um, step: [dx, dy], hex, roi_boxes: {id: (x0, y0, x1, y1)}} for maps;
    {size_px: [W, H], pixel_size_um} for images / mosaics. legends: the items placed on the view. view_state:
    {overlays, annotations, info_strip} = what the view shows; the Include boxes start from it (what you see is what
    you get). run_step is not used for the export itself (exports are not recipe steps)."""
    last = {**DEFAULTS, **(gs.get("export", kind) or {})}
    vs_ = view_state or {}
    shown = {"overlays": bool(vs_.get("overlays", bool(overlays))), "annotations": bool(vs_.get("annotations", True)),
             "info_strip": bool(vs_.get("info_strip", True))}
    folder = gs.get("export_folder") or last.get("folder") or ""
    if not folder or not Path(folder).is_dir():
        folder = str(pictures_dir())
    geometry = geometry or {}
    legends = legends or []
    is_map = kind == "map"
    if is_map:
        sample_id = p.sample_of(map_id)
        base = f"{map_id}_{(layer or {}).get('kind', 'map')}"
    elif kind == "mosaic":
        base = f"mosaic_{detector}"
    else:
        base = Path(image).stem if image else "image"
    if sample_id and not base.startswith(sample_id):
        base = f"{sample_id}_{base}"
    hexgrid = bool(geometry.get("hex"))
    with ui.dialog() as dlg, ui.card().classes("w-[40rem] max-w-full gap-3").mark("export_dialog"):
        ui.label("Export " + (f"{map_id}" if is_map else f"{detector} mosaic" if kind == "mosaic"
                              else Path(image or "").name)).classes("text-lg font-semibold")
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Format").classes("w-24 ti-muted")
            fmt_opts = {"png": "PNG", "tiff": "TIFF", "svg": "SVG", "pdf": "PDF",
                        **({} if is_map else {"bigtiff": "BigTIFF (tiled)"})}
            fmt = ui.toggle(fmt_opts, value=last["format"] if last["format"] in fmt_opts else "png") \
                .props("no-caps rounded unelevated toggle-color=primary").mark("exp_fmt")
        res_opts = ({"native": "native (1 px per measurement)", "2": "2×", "4": "4×", "8": "8×",
                     "px": "size in pixels", "width": "width in mm at DPI"} if is_map else
                    {"native": "1 (native, 1 px per image pixel)", "1/2": "1/2 of the original",
                     "1/4": "1/4 of the original", "1/8": "1/8 of the original", "px": "size in pixels",
                     "width": "width in mm at DPI"})
        with ui.row().classes("w-full items-end gap-2 no-wrap"):
            ui.label("Resolution").classes("w-24 ti-muted pb-2")
            res = ui.select(res_opts, value=last["res"] if last["res"] in res_opts else "native").classes("w-56") \
                .mark("exp_mode")
            if hexgrid:
                ui.icon("info", size="20px").classes("text-primary pb-2 cursor-help").tooltip(HEX_NOTE) \
                    .mark("exp_hex_info")
        with ui.row().classes("w-full items-end gap-2 no-wrap pl-24") as px_row:
            pw = ui.number("width [px]", value=last.get("px_w"), min=1, step=100, format="%d").classes("w-32") \
                .mark("exp_px_w")
            ui.icon("link", size="20px").classes("pb-3 ti-muted").tooltip("Aspect ratio locked")
            ph = ui.number("height [px]", value=None, min=1, step=100, format="%d").classes("w-32").mark("exp_px_h")
        with ui.row().classes("w-full items-end gap-2 no-wrap pl-24") as mm_row:
            wmm = ui.number("width [mm]", value=last["width_mm"], step=10, min=10).classes("w-32").mark("exp_wmm")
            dpi = ui.number("DPI", value=last["dpi"], step=50, min=72).classes("w-28").mark("exp_dpi")
        region = None
        if is_map:
            reg_opts = {"map": "whole map", "view": "current view", **{k: v for k, v in (rois or {}).items()}}
            with ui.row().classes("w-full items-end gap-2"):
                ui.label("Region").classes("w-24 ti-muted pb-2")
                region = ui.select(reg_opts, value=last["region"] if last["region"] in reg_opts else "map") \
                    .classes("w-56").mark("exp_region")
        size_lab = ui.label().classes("text-sm font-semibold").mark("exp_size")
        warn_box = ui.column().classes("w-full gap-1").mark("exp_size_warn")
        hex_lab = ui.label().classes("text-xs ti-muted").mark("exp_hex_note")
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("Include").classes("w-24 ti-muted")
            ov = ann = strip = None
            if is_map:
                ov = ui.checkbox(f"overlays ({', '.join(overlays or []) or 'none shown'})",
                                 value=shown["overlays"] and bool(overlays)).mark("exp_overlays")
                ann = ui.checkbox("annotations", value=shown["annotations"]).mark("exp_annotations")
            else:
                strip = ui.checkbox(INFO_BAR_LABEL, value=shown["info_strip"]).mark("exp_strip") \
                    .tooltip(INFO_BAR_TIP)
        leg_lab = ui.label().classes("text-xs ti-muted").mark("exp_legends")

        def legends_text(_=None):
            names = [LEGEND_NAMES[it["kind"]] for it in legends]
            strip_bar = (not is_map and strip is not None and strip.value
                         and any(it["kind"] == "scale_bar" for it in legends))
            if strip_bar:                 # the info bar has its own scale bar (user test, fix 16)
                names = [n for n in names if n != LEGEND_NAMES["scale_bar"]]
            leg_lab.text = ("Legends as placed on the view: " + (", ".join(names) if names else "none") +
                            ". Move, resize, show or hide them on the view; the export puts them exactly there." +
                            (" The scale bar comes from the microscope info bar (the one on the view is left "
                             "out, so there is only one)." if strip_bar else ""))
        legends_text()
        if strip is not None:
            strip.on_value_change(legends_text)
        with ui.row().classes("w-full items-end gap-2 no-wrap"):
            dest = ui.input("Save as", value=str(Path(folder) / (base + EXT.get(last["format"], ".png")))) \
                .classes("grow").mark("exp_dest")

            async def browse():
                f = await dialogs.save_file("Export as", Path(dest.value).name, str(Path(dest.value).parent),
                                            [TYPES[fmt.value]])
                if f:
                    dest.value = f
            ui.button("Browse…", icon="folder", on_click=browse).props("outline").mark("exp_browse")

        def region_um() -> tuple[float, float]:
            if not is_map:
                W, H = geometry.get("size_px") or (1, 1)
                ps = geometry.get("pixel_size_um") or 1.0
                return W * ps, H * ps
            x0, x1, y0, y1 = geometry.get("extent") or (0, 1, 0, 1)
            r = region.value if region else "map"
            if r == "view" and view:
                return abs(view["x1"] - view["x0"]), abs(view["y1"] - view["y0"])
            if r not in ("map", "view") and r in (geometry.get("roi_boxes") or {}):
                b = geometry["roi_boxes"][r]
                return abs(b[2] - b[0]), abs(b[3] - b[1])
            return x1 - x0, y1 - y0

        state = {"lock": False}

        def size() -> dict:
            step = tuple(geometry.get("step") or (geometry.get("pixel_size_um") or 1.0,) * 2)
            return export_size(res.value, region_um(), step, hexgrid, float(wmm.value or 120), float(dpi.value or 600),
                               pw.value, None if is_map else tuple(geometry.get("size_px") or (1, 1)))

        def update(_=None):
            px_row.set_visibility(res.value == "px")
            mm_row.set_visibility(res.value == "width")
            if res.value == "px" and not pw.value:          # start from the native width
                nat = export_size("native", region_um(), tuple(geometry.get("step") or (1, 1)), hexgrid, 1, 1, None,
                                  None if is_map else tuple(geometry.get("size_px") or (1, 1)))
                pw.value = nat["W"]
            s = size()
            if res.value == "px":
                state["lock"] = True
                ph.value = s["H"]
                state["lock"] = False
            ps_txt = (f"{s['px_um'] * 1000:.3g} nm" if s["px_um"] < 0.1 else f"{s['px_um']:.4g} µm") + \
                ("" if abs(s["px_um"] - s["py_um"]) < 1e-9 else f" × {s['py_um']:.4g} µm (not square)")
            size_lab.text = (f"Final size: {s['W']:,} × {s['H']:,} px · {s['w_um']:.4g} × {s['h_um']:.4g} µm · pixel "
                             f"{ps_txt} · ≈ {fmt_bytes(estimate_bytes(fmt.value, s['W'], s['H'], kind))} (estimate)")
            if hexgrid:
                k = (1 if res.value == "native" else int(res.value) if res.value in ("2", "4", "8") else
                     (geometry.get("step") or (1,))[0] / max(s["px_um"], 1e-12))
                hex_lab.text = ("Cells drawn as true hexagons." if k >= 4 else
                                "Cells drawn in the brick layout (rectangles, odd rows shifted by half a step).") + \
                    " Hover the ⓘ next to Resolution for the difference."
            else:
                hex_lab.text = ""
            if is_map:
                if s["W"] * s["H"] > 400e6:
                    size_lab.text += " – very large; consider a smaller size"
                return
            size_check(s)

        def size_check(s: dict):
            """Images / mosaics: pixels and memory BEFORE the export (fix 15); refused sizes offer 1/2, 1/4, 1/8 or
            the tiled BigTIFF instead of failing."""
            from ..ops_stitch import export_estimate  # noqa: PLC0415

            nat = tuple(geometry.get("size_px") or (1, 1))
            est = export_estimate(nat, (s["W"], s["H"]), fmt.value, int(geometry.get("bytes_per_px") or 1))
            state["est"] = est
            warn_box.clear()
            if est["level"] == "ok":
                if state.get("btn"):
                    state["btn"].set_enabled(True)
                return
            refuse = est["level"] == "refuse"
            with warn_box, ui.column().classes("w-full gap-1 p-2 rounded border " + (
                    "bg-red-50 border-red-300" if refuse else "bg-amber-50 border-amber-300")):
                ui.label(("Cannot export: " if refuse else "Large export: ") + est["message"] +
                         f" (≈ {fmt_bytes(est['memory_bytes'])} of memory)").classes("text-xs").mark("exp_warn_text")
                with ui.row().classes("gap-2"):
                    for a in est["alternatives"][:3]:
                        ui.button(f"Use {a['fraction']} ({a['W']:,} × {a['H']:,} px)",
                                  on_click=lambda a=a: setattr(res, "value", a["fraction"])) \
                            .props("outline dense no-caps").mark(f"exp_use_{a['fraction'].replace('/', '_')}")
                    if fmt.value != "bigtiff":
                        ui.button("Use BigTIFF (tiled)", on_click=lambda: setattr(fmt, "value", "bigtiff")) \
                            .props("outline dense no-caps").mark("exp_use_bigtiff")
            if state.get("btn"):
                state["btn"].set_enabled(not refuse)

        def h_typed(e):
            if state["lock"] or res.value != "px" or not e.value:
                return
            w_um, h_um = region_um()
            pw.value = max(1, int(round(float(e.value) * w_um / max(h_um, 1e-9))))
        for w in (res, wmm, dpi, pw, fmt):
            w.on_value_change(update)
        if region is not None:
            region.on_value_change(update)
        ph.on_value_change(h_typed)

        def fix_ext():
            d = Path(dest.value)
            dest.value = str(d.with_suffix(EXT[fmt.value]))
        fmt.on_value_change(fix_ext)
        info = ui.label().classes("text-xs ti-muted")
        info.text = ("PNG / TIFF keep the pixel size in the file and a JSON sidecar; SVG / PDF are figures drawn by the "
                     "backend renderer." if is_map else "A JSON sidecar with the pixel size is written next to it.")
        update()

        def choices() -> dict:
            c = {"format": fmt.value, "res": res.value, "width_mm": float(wmm.value or 120), "dpi": int(dpi.value or 600),
                 "px_w": pw.value}
            if is_map:
                c.update(overlays=ov.value, annotations=ann.value, region=region.value)
            else:
                c.update(info_strip=strip.value)
            return c

        async def go():
            c = choices()
            gs.set("export", kind, {k: v for k, v in c.items() if k in DEFAULTS})
            target = Path(dest.value)
            if not target.parent.is_dir():
                ui.notify(f"Folder {target.parent} does not exist.", type="warning")
                return
            s = size()
            if not is_map and (state.get("est") or {}).get("level") == "refuse":
                ui.notify("This size cannot be exported: press 'Use 1/2', 'Use 1/4', 'Use 1/8' or 'Use BigTIFF "
                          "(tiled)' in this dialog.", type="warning")
                return
            dlg.close()
            if c["res"] == "px":                            # an exact pixel width = a figure / resize of that width
                c["res"], c["dpi"], c["width_mm"] = "width", 300, s["W"] / 300 * 25.4
            if is_map:
                prm = {"map_id": map_id, "layer": layer, "overlays": overlays if c["overlays"] else [],
                       "annotations": c["annotations"], "format": c["format"], "name": target.stem,
                       "scale_bar": False, "colorbar": False, "legend": False, "title": False,
                       "legends": legends, "overlay_style": overlay_style or {}}
                if c["res"] == "native":
                    prm["mode"] = "native"
                elif c["res"] == "width":
                    prm.update(mode="width", width_mm=c["width_mm"], dpi=c["dpi"])
                else:
                    prm.update(mode="scale", scale=int(c["res"]))
                if c["region"] == "view" and view:
                    prm["roi"] = (view["x0"], view["y0"], view["x1"], view["y1"])
                elif c["region"] not in ("map", "view"):
                    prm["roi_id"] = c["region"]
                op = "export.map"
            else:
                prm = {"detector": detector, "format": c["format"],
                       "mode": ("native" if c["res"] == "native" else "scale" if c["res"] in FRACTIONS else "width"),
                       "scale": 1.0 / FRACTIONS.get(c["res"], 1), "width_mm": c["width_mm"], "dpi": c["dpi"],
                       "info_strip": c["info_strip"], "name": target.stem, "sample_id": sample_id,
                       "legends": legends, "scale_bar": False}
                prm.update({"stitch": stitch_step} if kind == "mosaic" else {"image": image})
                op = "sem.export"
            gs.set("export_folder", str(target.parent))           # remembered per project (fix 3.3)
            await run_export(p, op, prm, target)

        earlier = [e for e in reversed(exportlog.load(p.root))
                   if e.op == ("export.map" if is_map else "sem.export")
                   and (not is_map or e.map_id == map_id)][:8]
        if earlier:
            with ui.expansion(f"Earlier exports ({len(earlier)})", icon="history").classes("w-full text-sm") \
                    .mark("exp_earlier"):
                for e in earlier:
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        with ui.column().classes("grow min-w-0 gap-0"):
                            ui.label(e.label()).classes("text-sm truncate")
                            ui.label(f"{e.date.replace('T', ' ')} · {Path(e.file).name if e.file else 'kept in the project'}"
                                     ).classes("text-xs ti-muted truncate")
                        ui.button("Export again", icon="replay",
                                  on_click=lambda e=e: again(e)).props("flat dense no-caps") \
                            .mark(f"exp_again_{e.id}").tooltip("Export again with exactly these settings (to the same "
                                                               "file, or the 'Save as' file above if it had none)")

        async def again(e):
            dlg.close()
            t = Path(e.file) if e.file else Path(dest.value).with_suffix(EXT.get(e.params.get("format", "png"), ".png"))
            await run_export(p, e.op, e.params, t, e.backend)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat").mark("exp_cancel")
            state["btn"] = ui.button("Export", icon="ios_share", on_click=go).mark("run_export")
        if not is_map:
            update()                                   # enable / disable Export from the size check
    dlg.open()


async def run_export(p: Project, op: str, params: dict, target: Path, backend: str | None = None) -> None:
    """Run one export (not a recipe step) with a spinner, copy it to `target`, log it; say where it went."""
    n = ui.notification(f"Exporting {target.name} …", spinner=True, timeout=None)
    try:
        res = await ng_run.io_bound(service.export, p, op, params, backend, target)
    except Exception as e:  # noqa: BLE001
        error(e, "Export failed")
        return
    finally:
        n.dismiss()
    if res is None:                                   # app shutting down
        return
    notify_actions(f"Exported {target.name} to {target.parent} (listed under 'Earlier exports' in the Export dialog)",
                   [("Show", f"() => fetch('/api/ti64/reveal?path={_q(target)}', {{method: 'POST'}})")],
                   type="positive", multi_line=False)


def _q(path: Path) -> str:
    from urllib.parse import quote  # noqa: PLC0415

    return quote(str(path))
