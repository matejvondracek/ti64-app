"""Export dialog, shared by the EBSD tab (maps: export.map), the Stitch tab (mosaics: sem.export) and the View tab
(single images / mosaics: sem.export).

Format PNG / TIFF / SVG / PDF; resolution native (1 px per measurement / image pixel), 2x / 4x / 8x (maps), a size
typed in pixels (width OR height, the aspect ratio is locked) or a width in mm at a chosen DPI. The dialog shows the
final size in pixels, the physical size in um and an estimated file size. Legends (scale bar, colour bar, IPF key)
are NOT chosen here: the export shows exactly the ones placed on the map / image view (what you see is what you get).
Location by the Windows save dialog. The export is a recipe step (its output stays in the project) and is copied to
the chosen file. The last choices are remembered (gui_state.json of the project)."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from nicegui import ui

from ..project import Project
from . import dialogs
from .shell import error
from .state import GuiState

EXT = {"png": ".png", "tiff": ".tif", "svg": ".svg", "pdf": ".pdf"}
TYPES = {"png": ("PNG image", "*.png"), "tiff": ("TIFF image", "*.tif;*.tiff"), "svg": ("SVG vector", "*.svg"),
         "pdf": ("PDF document", "*.pdf")}
DEFAULTS = {"format": "png", "res": "native", "width_mm": 120.0, "dpi": 600, "px_w": None, "overlays": True,
            "annotations": True, "info_strip": True, "region": "map", "folder": ""}
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
                        legends: list[dict] | None = None, overlay_style: dict | None = None) -> None:
    """geometry: {extent: [x0, x1, y0, y1] um, step: [dx, dy], hex, roi_boxes: {id: (x0, y0, x1, y1)}} for maps;
    {size_px: [W, H], pixel_size_um} for images / mosaics. legends: the items placed on the view."""
    last = {**DEFAULTS, **(gs.get("export", kind) or {})}
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
            fmt = ui.toggle({"png": "PNG", "tiff": "TIFF", "svg": "SVG", "pdf": "PDF"}, value=last["format"]) \
                .props("no-caps rounded unelevated toggle-color=primary").mark("exp_fmt")
        res_opts = ({"native": "native (1 px per measurement)", "2": "2×", "4": "4×", "8": "8×",
                     "px": "size in pixels", "width": "width in mm at DPI"} if is_map else
                    {"native": "native (1 px per image pixel)", "px": "size in pixels", "width": "width in mm at DPI"})
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
        hex_lab = ui.label().classes("text-xs ti-muted").mark("exp_hex_note")
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("Include").classes("w-24 ti-muted")
            ov = ann = strip = None
            if is_map:
                ov = ui.checkbox(f"overlays ({', '.join(overlays or []) or 'none shown'})", value=last["overlays"]) \
                    .mark("exp_overlays")
                ann = ui.checkbox("annotations", value=last["annotations"]).mark("exp_annotations")
            else:
                strip = ui.checkbox("info strip / data bar", value=last["info_strip"]).mark("exp_strip")
        ui.label("Legends as placed on the view: " + (", ".join(LEGEND_NAMES[it["kind"]] for it in legends)
                                                      if legends else "none") +
                 ". Move, resize, show or hide them on the view; the export puts them exactly there.") \
            .classes("text-xs ti-muted").mark("exp_legends")
        with ui.row().classes("w-full items-end gap-2 no-wrap"):
            dest = ui.input("Save as", value=str(Path(last["folder"] or Path.home() / "Documents") /
                                                 (base + EXT[last["format"]]))).classes("grow").mark("exp_dest")

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
            if s["W"] * s["H"] > 400e6:
                size_lab.text += " – very large; consider a smaller size"

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
                 "px_w": pw.value, "folder": str(Path(dest.value).parent)}
            if is_map:
                c.update(overlays=ov.value, annotations=ann.value, region=region.value)
            else:
                c.update(info_strip=strip.value)
            return c

        async def go():
            c = choices()
            gs.set("export", kind, c)
            target = Path(dest.value)
            if not target.parent.is_dir():
                ui.notify(f"Folder {target.parent} does not exist.", type="warning")
                return
            s = size()
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
                step = await run_step("export.map", prm, "export", None, with_map=False)
            else:
                prm = {"detector": detector, "format": c["format"],
                       "mode": "native" if c["res"] == "native" else "width", "width_mm": c["width_mm"], "dpi": c["dpi"],
                       "info_strip": c["info_strip"], "name": target.stem, "sample_id": sample_id,
                       "legends": legends, "scale_bar": False}
                prm.update({"stitch": stitch_step} if kind == "mosaic" else {"image": image})
                step = await run_step("sem.export", prm, "export", None, with_map=False)
            if not step:
                return
            try:
                src = p.root / step.outputs["export"].path
                shutil.copy2(src, target)
                side = p.root / step.outputs["sidecar"].path
                shutil.copy2(side, target.with_suffix(".json"))
                ui.notify(f"Exported {target.name} to {target.parent}", type="positive",
                          actions=[{"label": "Show", "color": "white",
                                    ":handler": f"() => fetch('/api/ti64/reveal?path={_q(target)}', {{method: 'POST'}})"}])
            except Exception as e:  # noqa: BLE001
                error(e, "Could not write the exported file")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat").mark("exp_cancel")
            ui.button("Export", icon="ios_share", on_click=go).mark("run_export")
    dlg.open()


def _q(path: Path) -> str:
    from urllib.parse import quote  # noqa: PLC0415

    return quote(str(path))
