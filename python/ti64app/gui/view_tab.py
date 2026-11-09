"""View tab: open any image or mosaic of the project (JEOL .bmp / .tif / .png, stitched mosaics) with zoom / pan, a
metadata panel, a scale bar (from the pixel size) and Export; .up2 pattern files open here too (header table and a
pattern viewer: the pattern at a chosen scan row / column, step through the neighbours). Indexing of the patterns
comes in phase 9."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
from nicegui import run as ng_run
from nicegui import ui

from .. import service
from ..project import Project
from . import tiles
from .export_dialog import export_dialog
from .shell import check_interrupted, error, header
from .state import GuiState, stable
from ..legends import default_item
from .viewer import MapViewer

IMAGE_KINDS = ("image",)


def view_sources(p: Project) -> dict[str, str]:
    """What the View tab can open: linked images and .up2 files (by sample) and the stitched mosaics of the project."""
    out: dict[str, str] = {}
    files = [m for m in p.files() if m.kind in ("image", "up2")]
    files.sort(key=lambda m: (m.sample_id is None, m.sample_id or "", m.kind, Path(m.file).name.lower()))
    for m in files:
        out[f"file:{m.id}"] = f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}"
    for s in reversed(p.recipe().steps):
        if s.op != "sem.stitch" or s.stale:
            continue
        for det in ("BSE", "SE"):
            if f"mosaic_{det}" in s.outputs:
                out[f"mosaic:{s.id}:{det}"] = f"{s.sample_id or 'Unassigned'} / {det} mosaic (stitch {s.id})"
    return out


def _png_data_url(a: np.ndarray, scale: int = 6) -> str:
    from PIL import Image  # noqa: PLC0415

    a = a.astype(np.float32)
    lo, hi = float(np.percentile(a, 0.5)), float(np.percentile(a, 99.5))
    g = np.clip((a - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
    im = Image.fromarray(g).resize((g.shape[1] * scale, g.shape[0] * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def load_image_source(p: Project, key: str, with_bar: bool = False) -> dict:
    """Pixels + metadata of a View source: {img, ps, rows (metadata table), title, file, strip_px, kind}."""
    from ..modules import stitching  # noqa: PLC0415

    if key.startswith("file:"):
        m = p.file(key[5:])
        if m is None:
            raise KeyError(f"file {key[5:]} is not in the project any more")
        if not Path(m.file).is_file():
            raise FileNotFoundError(m.file)
        t = stitching.read_tile(m.file)
        from ..io.bse import read_image  # noqa: PLC0415

        img = read_image(m.file)
        if not with_bar:
            img = img[: t.height]
        rows = [("file", Path(m.file).name), ("folder", str(Path(m.file).parent)),
                ("detector", f"{t.detector_class} ({t.detector or '–'})"),
                ("magnification", f"{t.magnification:g}×" if t.magnification else "–"),
                ("pixel size", f"{t.pixel_size_um * 1000:.3g} nm" if t.pixel_size_um else "unknown (no .txt)"),
                ("image", f"{t.width} × {t.height} px" + (f" + {t.strip_px} px data bar" if t.strip_px else "")),
                ("field of view", f"{t.width * t.pixel_size_um:.4g} × {t.height * t.pixel_size_um:.4g} µm"
                 if t.pixel_size_um else "–"),
                ("accelerating voltage", f"{t.kv:g} kV" if t.kv else "–"),
                ("working distance", f"{t.wd_mm:g} mm" if t.wd_mm else "–"),
                ("stage X / Y / Z", " / ".join(f"{v:g}" for v in (t.stage_x_mm, t.stage_y_mm, t.stage_z_mm)
                                                if v is not None) + " mm" if t.has_stage else "–"),
                ("stage R / T", f"{t.stage_rotation_deg:g}° / {t.stage_tilt_deg:g}°"
                 if t.stage_rotation_deg is not None and t.stage_tilt_deg is not None else "–"),
                ("image number", str(t.image_number) if t.image_number is not None else "–"),
                ("date", f"{t.date} {t.time}".strip() or "–"), ("sidecar", Path(t.txt).name if t.txt else "none")]
        return {"img": img, "ps": t.pixel_size_um, "rows": rows, "file": m.file, "strip_px": t.strip_px,
                "title": f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}", "kind": "image",
                "sample_id": m.sample_id, "token": f"{m.file}|{Path(m.file).stat().st_mtime}|{with_bar}"}
    _, sid, det = key.split(":")
    s = p.recipe().step(sid)
    side = json.loads((p.root / s.outputs[f"sidecar_{det}"].path).read_text(encoding="utf-8"))
    import tifffile  # noqa: PLC0415

    img = tifffile.imread(p.root / s.outputs[f"mosaic_{det}"].path)
    if img.ndim > 2:
        img = img[..., 0]
    h = int(side["mosaic_px"][1])
    if not with_bar:
        img = img[:h]
    ps = side.get("pixel_size_um")
    mis = (side.get("misalignment_px") or {}).get("max")
    rows = [("mosaic", f"{det}, {side['n_tiles']} tiles (stitch step {sid})"),
            ("size", f"{side['mosaic_px'][0]} × {side['mosaic_px'][1]} px"
             + (f" + {side['strip']['height_px']} px info strip" if side['strip'].get('height_px') else "")),
            ("pixel size", f"{ps * 1000:.3g} nm" if ps else "unknown"),
            ("field", f"{side['mosaic_px'][0] * ps:.4g} × {side['mosaic_px'][1] * ps:.4g} µm" if ps else "–"),
            ("magnification", ", ".join(f"{v:g}×" for v in side["settings"]["magnification"]) or "–"),
            ("blending", f"{side['blend']}" + (f", {side.get('render', {}).get('seam')} seam" if side.get("render") else "")),
            ("residual / misalignment", f"{side['residual_px']['max']} px / {mis} px" if mis is not None
             else f"{side['residual_px']['max']} px"),
            ("tiles placed by stage only", ", ".join(side["stage_only_tiles"]) or "none"),
            ("inputs", ", ".join(Path(x["image"]).name for x in side["inputs"][:6])
             + (" …" if len(side["inputs"]) > 6 else ""))]
    return {"img": img, "ps": ps, "rows": rows, "file": str(p.root / s.outputs[f"mosaic_{det}"].path),
            "strip_px": int(side["strip"].get("height_px") or 0), "title": f"{s.sample_id or 'Unassigned'} / {det} "
            f"mosaic", "kind": "mosaic", "sample_id": s.sample_id, "stitch": sid, "detector": det,
            "token": f"{sid}|{det}|{with_bar}"}


def view_page() -> None:
    p = header("View")
    if p is None:
        ui.navigate.to("/")
        return
    gs = GuiState(p)
    gs.set("tab", "/view")
    check_interrupted(p)
    opts = view_sources(p)
    want = gs.get("view_file")
    key0 = f"file:{want}" if want and f"file:{want}" in opts else (gs.get("view_source") if gs.get("view_source") in opts
                                                                    else next(iter(opts), None))
    st: dict = {"key": key0, "src": None, "viewer": None, "timers": []}

    with ui.row().classes("w-full no-wrap gap-0 items-stretch").style("height: calc(100vh - 56px)"):
        left = ui.column().classes("h-full shrink-0 bg-white border-r gap-0").style("width: 360px").mark("view_left")
        right = ui.column().classes("h-full grow gap-0 min-w-0")
    with left:
        with ui.column().classes("w-full p-3 gap-2"):
            ui.label("View").classes("text-lg font-semibold")
            src = ui.select(opts, value=key0, label="Sample / file").classes("w-full") \
                .props("options-dense").mark("view_source") \
                .tooltip("Images, mosaics and .up2 pattern files of this project (add files on the Files tab)")
        with ui.scroll_area().classes("w-full grow"):
            meta = ui.column().classes("w-full gap-1 px-3 pb-3").mark("view_meta")
        with ui.row().classes("w-full p-3 gap-2 no-wrap border-t"):
            exp_btn = ui.button("Export", icon="ios_share", on_click=lambda: do_export()).classes("grow") \
                .mark("view_export")
    with right:
        bar = ui.row().classes("w-full items-center gap-3 px-3 py-2 no-wrap bg-white border-b")
        with bar:
            title = ui.label("").classes("font-semibold truncate grow").mark("view_title")
            sbar = ui.checkbox("scale bar", value=gs.get("view_opts", "scale_bar", default=True)).mark("view_sbar") \
                .tooltip("Show the scale bar (from the pixel size in the JEOL .txt / mosaic sidecar)")
            with_bar = ui.checkbox("data bar", value=gs.get("view_opts", "data_bar", default=False)) \
                .mark("view_databar").tooltip("Show the JEOL data bar / info strip below the image")
        holder = ui.column().classes("w-full grow gap-0 min-h-0")
        status = ui.label("").classes("text-xs ti-muted px-3 py-1 bg-white border-t w-full").mark("view_status")

    if not opts:
        with holder:
            with ui.column().classes("p-8 gap-3"):
                ui.label("No images, mosaics or .up2 files in this project yet.").classes("text-lg")
                ui.label("Add SEM images or pattern files on the Files tab, or stitch images on the Stitch tab.")
                ui.button("Go to the Files tab", icon="folder", on_click=lambda: ui.navigate.to("/files"))
        exp_btn.disable()
        return

    def meta_table(rows):
        meta.clear()
        with meta:
            with ui.grid(columns=2).classes("gap-x-3 gap-y-1 text-sm w-full").style("grid-template-columns: auto 1fr"):
                for k, v in rows:
                    ui.label(k).classes("ti-muted whitespace-nowrap")
                    ui.label(str(v)).style("overflow-wrap: anywhere")

    async def show():
        key = st["key"]
        gs.set("view_source", key)
        for t in st["timers"]:                       # progress polling of a previous .up2 overview pass
            t.deactivate()
        st["timers"].clear()
        with_bar.set_visibility(True)
        if key and key.startswith("file:"):
            m = p.file(key[5:])
            gs.set("view_file", m.id if m else None)
            if m is not None and m.kind == "up2":
                show_up2(m)
                return
        bar.set_visibility(True)
        try:
            d = await ng_run.io_bound(load_image_source, p, key, bool(with_bar.value))
        except Exception as e:  # noqa: BLE001
            error(e, "Could not open the image")
            return
        st["src"] = d
        meta_table(d["rows"])
        title.text = d["title"]
        ps = d["ps"] or 1.0
        lay = tiles.ImageLayer(str(abs(hash(d["token"]))), d["img"], ps)
        tok = tiles.register_image(lay)
        holder.clear()
        with holder:
            v = MapViewer(ps, lay.extent).classes("w-full h-full").mark("view_viewer")
            v._props["initial_view"] = gs.get("view_zoom", key)
            v.on("view", lambda e: gs.set("view_zoom", key, {k: e.args[k] for k in ("x0", "x1", "y0", "y1")}))
            v.on("cursor", throttle=0.15, handler=lambda e: status.set_text(
                f"x {e.args['x'] / ps:.0f} px, y {e.args['y'] / ps:.0f} px"
                + (f"  ({e.args['x']:.3f}, {e.args['y']:.3f} µm)" if d["ps"] else "")))
        v.on("legends", on_legends)
        v.set(layer_url=tiles.image_url(tok), legends=legends() if d["ps"] else [], legend_data={})
        st["viewer"] = v
        status.text = (f"{d['img'].shape[1]} × {d['img'].shape[0]} px" +
                       (f", {d['ps'] * 1000:.3g} nm per pixel" if d["ps"] else ", no pixel size: scale bar off"))
        exp_btn.enable()

    def show_up2(m):
        """.up2: overview map (pattern quality / virtual BSE from one streaming pass, cached) in the viewer; a click
        loads only the pattern under the cursor (fix 13)."""
        from ..io import up2  # noqa: PLC0415
        from ..modules import up2_overview as uo  # noqa: PLC0415

        bar.set_visibility(True)
        with_bar.set_visibility(False)
        st["src"] = None
        st["viewer"] = None
        exp_btn.disable()
        try:
            h = up2.read_header(m.file)
        except Exception as e:  # noqa: BLE001
            error(e, "Could not read the .up2 header")
            return
        title.text = f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}"
        meta_table([("file", Path(m.file).name), ("folder", str(Path(m.file).parent)),
                    ("format version", h.version), ("patterns", f"{h.n_patterns:,}"),
                    ("pattern size", f"{h.pattern_width} × {h.pattern_height} px (16 bit)"),
                    ("scan grid", f"{'hexagonal' if h.hex_grid else 'square'}, {h.n_cols} columns × {h.n_rows} rows"),
                    ("step", f"{h.x_step_um:g} × {h.y_step_um:g} µm"),
                    ("scan size", f"{(h.n_cols - 1) * h.x_step_um:g} × {(h.n_rows - 1) * h.y_step_um:g} µm"),
                    ("file size", f"{h.file_size / 1e9:.2f} GB"
                                  + ("" if h.size_ok else " (does not match the header!)")),
                    ("data offset", f"{h.data_offset} bytes")])
        cache = p.cache_dir
        o = dict(gs.get("up2_overview", m.id, default=None) or {})
        o.setdefault("step", 2)
        o.setdefault("channel", "iq")
        o.setdefault("flatten", True)
        o.setdefault("corrected", True)
        pos = gs.get("up2_pos", m.id, default=[h.n_rows // 2, h.n_cols // 2])
        u = {"job": None, "ov": None, "timer": None, "busy": False}

        def save_opts():
            gs.set("up2_overview", m.id, dict(o))
        with meta:
            ui.label("Overview map: computed once by streaming the file in chunks (never loaded fully) and cached in "
                     "the project's cache folder. Click a point of the map to load that one pattern.") \
                .classes("text-xs ti-muted pt-2").mark("up2_note")

        holder.clear()
        with holder:
            with ui.row().classes("w-full h-full no-wrap gap-0 items-stretch").mark("up2_viewer"):
                with ui.column().classes("grow h-full min-w-0 gap-0"):
                    with ui.row().classes("w-full items-center gap-3 px-3 py-1 bg-white border-b").mark("up2_toolbar"):
                        chan = ui.select(uo.CHANNELS, value=o["channel"], label="Map").classes("w-56") \
                            .props("dense options-dense").mark("up2_channel") \
                            .tooltip("Pattern quality: Krieger Lassen image quality after static + dynamic background "
                                     "correction. Virtual BSE: mean intensity of the whole pattern.")
                        flat = ui.checkbox("flatten shading", value=o["flatten"]).props("dense").mark("up2_flatten") \
                            .tooltip("Divide by a strongly blurred copy of the map: removes the smooth shading from the "
                                     "detector geometry and keeps the microstructure contrast (display only)")
                        res = ui.select({4: "every 4th row and column (quick)", 2: "every 2nd row and column",
                                         1: "full resolution (all patterns)"}, value=o["step"], label="Resolution") \
                            .classes("w-60").props("dense options-dense").mark("up2_resolution") \
                            .tooltip("Skipped rows are never read: every 2nd row reads half the file. Full resolution "
                                     f"reads all {h.file_size / 1e9:.0f} GB (several minutes).")
                        go = ui.button("Compute overview", icon="play_arrow", on_click=lambda: start()) \
                            .props("dense no-caps").classes("px-3").mark("up2_compute")
                        stop = ui.button("Cancel", icon="stop", on_click=lambda: cancel()) \
                            .props("dense outline no-caps").classes("px-3").mark("up2_cancel")
                    with ui.row().classes("w-full items-center gap-3 px-3 py-1 no-wrap bg-white border-b"):
                        prog = ui.linear_progress(value=0, show_value=False).classes("grow").mark("up2_progress")
                        ptext = ui.label("").classes("text-xs ti-muted whitespace-nowrap").mark("up2_progress_text")
                    mapbox = ui.column().classes("w-full grow min-h-0 gap-0")
                with ui.column().classes("h-full shrink-0 bg-white border-l p-3 gap-2").style("width: 320px"):
                    ui.label("Pattern").classes("font-semibold")
                    img = ui.image().classes("border").style("width: 260px; height: 260px; "
                                                             "image-rendering: pixelated").mark("up2_pattern")
                    corr = ui.checkbox("background corrected", value=o["corrected"]).props("dense") \
                        .mark("up2_corrected") \
                        .tooltip("Show the pattern minus the static background (mean pattern of the file) and minus "
                                 "its own blurred copy (dynamic background), as used for the pattern quality. Off = "
                                 "raw counts. Needs the overview (it holds the mean pattern).")
                    info = ui.label("").classes("text-sm").style("white-space: pre-line").mark("up2_info")
                    with ui.row().classes("items-end gap-2 no-wrap"):
                        row = ui.number("scan row", value=pos[0], min=0, max=h.n_rows - 1, step=1, format="%d") \
                            .classes("w-28").props("dense").mark("up2_row")
                        col = ui.number("scan column", value=pos[1], min=0, max=h.n_cols - 1, step=1,
                                        format="%d").classes("w-28").props("dense").mark("up2_col")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label("Step to a neighbour").classes("text-xs ti-muted")
                        with ui.grid(columns=3).classes("gap-0"):
                            for lab, dr, dc, ic in (("", 0, 0, ""), ("up", -1, 0, "north"), ("", 0, 0, ""),
                                                    ("left", 0, -1, "west"), ("", 0, 0, ""),
                                                    ("right", 0, 1, "east"), ("", 0, 0, ""), ("down", 1, 0, "south"),
                                                    ("", 0, 0, "")):
                                if lab:
                                    ui.button(icon=ic, on_click=lambda dr=dr, dc=dc: step(dr, dc)) \
                                        .props("flat round dense size=sm").tooltip(f"pattern {lab}") \
                                        .mark(f"up2_{lab}")
                                else:
                                    ui.element("div")

        def placeholder(text: str):
            st["viewer"] = None
            mapbox.clear()
            with mapbox:
                with ui.column().classes("w-full h-full items-center justify-center gap-2 bg-gray-50") \
                        .mark("up2_placeholder"):
                    ui.icon("grid_on", size="48px").classes("ti-muted")
                    ui.label(text).classes("text-center ti-muted").style("max-width: 420px")

        def marker(r, c):
            v = st["viewer"]
            if v is not None:
                v.set(shapes=[{"id": "up2_sel", "type": "point", "text": "", "width_pt": 1, "color": "#e53935",
                               "xy": [[(c + 0.5 + (0.5 if h.hex_grid and r % 2 else 0)) * h.x_step_um,
                                       (r + 0.5) * h.y_step_um]]}])

        def draw():
            if u["busy"]:
                return
            r = min(max(int(row.value or 0), 0), h.n_rows - 1)
            c = min(max(int(col.value or 0), 0), up2.row_length(h, r) - 1)
            try:
                a = up2.read_pattern(m.file, r, c, h)
            except Exception as e:  # noqa: BLE001
                info.text = f"no pattern: {e}"
                return
            shown = a
            if corr.value and u["ov"] is not None and u["ov"].background is not None:
                shown = uo.corrected_pattern(a, u["ov"].background)
            img.set_source(_png_data_url(shown, 5))
            x, y = up2.pattern_position(h, r, c)
            txt = (f"row {r}, column {c}\nx {x:g} µm, y {y:g} µm\npattern {up2.pattern_index(h, r, c):,} of "
                   f"{h.n_patterns:,}\nintensity {int(a.min())}–{int(a.max())} (mean {a.mean():.0f})")
            ov = u["ov"]
            if ov is not None:
                orow, ocol = r // ov.step, c // ov.step
                if orow < ov.iq.shape[0] and ocol < ov.iq.shape[1] and np.isfinite(ov.iq[orow, ocol]):
                    txt += f"\npattern quality {ov.iq[orow, ocol]:.3f}" + (
                        f" (of the overview point at row {orow * ov.step}, column {ocol * ov.step})"
                        if ov.step > 1 else "")
            info.text = txt
            gs.set("up2_pos", m.id, [r, c])
            marker(r, c)

        def goto(r, c):
            u["busy"] = True                     # one draw for both fields
            row.value, col.value = r, c
            u["busy"] = False
            draw()

        def step(dr, dc):
            r = min(max(int(row.value or 0) + dr, 0), h.n_rows - 1)
            c = min(max(int(col.value or 0) + dc, 0), up2.row_length(h, r) - 1)
            goto(r, c)

        def scan_at(x, y):
            r = min(max(int(np.floor(y / h.y_step_um)), 0), h.n_rows - 1)
            sx = 0.5 if h.hex_grid and r % 2 else 0.0
            c = min(max(int(np.floor(x / h.x_step_um - sx)), 0), up2.row_length(h, r) - 1)
            return r, c

        def on_probe(e):
            x, y = e.args["xy"][0]
            goto(*scan_at(x, y))

        def on_cursor(e):
            r, c = scan_at(e.args["x"], e.args["y"])
            x, y = up2.pattern_position(h, r, c)
            status.text = f"row {r}, column {c}  (x {x:g} µm, y {y:g} µm)  · click to load this pattern"

        def render():
            ov = u["ov"]
            if ov is None:
                return
            psx, _ = ov.pixel_um
            lay = tiles.ImageLayer(f"up2_{abs(hash((m.file, ov.step, ov.rows_done, o['channel'], o['flatten'])))}",
                                   ov.image(o["channel"], flatten=o["flatten"]), psx)
            tok = tiles.register_image(lay)
            mapbox.clear()
            with mapbox:
                v = MapViewer(psx, lay.extent).classes("w-full h-full").mark("up2_map")
                key = f"up2:{m.id}"
                v._props["initial_view"] = gs.get("view_zoom", key)
                v.on("view", lambda e: gs.set("view_zoom", key, {k: e.args[k] for k in ("x0", "x1", "y0", "y1")}))
                v.on("cursor", throttle=0.15, handler=on_cursor)
                v.on("probe", on_probe)
                v.on("legends", on_legends)
            v.set(layer_url=tiles.image_url(tok), tool="probe", legends=legends(), legend_data={})
            st["viewer"] = v
            draw()

        def set_running(running: bool):
            go.set_enabled(not running)
            stop.set_enabled(running)
            res.set_enabled(not running)

        def watch(j):
            u["job"] = j
            set_running(True)
            if u["timer"] is None:
                u["timer"] = ui.timer(0.5, poll)
                st["timers"].append(u["timer"])
            else:
                u["timer"].activate()

        def poll():
            j = u["job"]
            if j is None:
                return
            prog.value = j.fraction
            ptext.text = j.text
            if not j.done:
                return
            u["timer"].deactivate()
            u["job"] = None
            set_running(False)
            if j.error:
                part = uo.load_cached(cache, m.file, o["step"])
                ptext.text = j.error
                if part is not None and part.rows_done:
                    u["ov"] = part              # show what is done (black below)
                    render()
                    ui.notify("Overview cancelled. The rows done are kept; 'Compute overview' (above the map) "
                              "continues from there.", type="info")
                else:
                    placeholder("Overview not computed. Press 'Compute overview' above the map.")
                    if "cancelled" not in j.error:
                        error(RuntimeError(j.error), "The overview could not be computed")
                return
            u["ov"] = j.result
            ptext.text = (f"{j.result.iq.shape[1]} × {j.result.iq.shape[0]} points, "
                          f"{j.result.pixel_um[0]:g} µm per point, computed in {j.result.seconds:.0f} s")
            render()

        def start():
            if u["job"] is not None:
                return
            if u["ov"] is None or not u["ov"].rows_done:
                placeholder("Computing the overview map… (progress bar above the map). You can already step "
                            "through patterns with the 'scan row' / 'scan column' fields of the 'Pattern' panel.")
            watch(uo.start_job(m.file, cache, o["step"]))

        def cancel():
            j = u["job"]
            if j is not None:
                j.cancel.set()
                ptext.text = "cancelling after the current chunk…"

        def load_or_start():
            u["ov"] = uo.load_cached(cache, m.file, o["step"])
            running = uo.running_job(m.file, cache, o["step"])
            if running is not None:                 # an earlier visit is still computing it: attach
                placeholder("Computing the overview map… (progress bar above the map).")
                watch(running)
            elif u["ov"] is not None and u["ov"].complete:
                set_running(False)
                prog.value = 1
                ptext.text = (f"{u['ov'].iq.shape[1]} × {u['ov'].iq.shape[0]} points, {u['ov'].pixel_um[0]:g} µm per "
                              f"point (cached, computed in {u['ov'].seconds:.0f} s)")
                render()
            else:
                if u["ov"] is not None:             # partial pass: show it, continue
                    prog.value = u["ov"].rows_done / u["ov"].iq.shape[0]
                    render()
                start()                             # first open: compute once (cancellable)

        def chan_change(e):
            o["channel"] = e.value
            save_opts()
            render()

        def flat_change(e):
            o["flatten"] = bool(e.value)
            save_opts()
            render()

        def res_change(e):
            o["step"] = int(e.value)
            save_opts()
            u["ov"] = None
            load_or_start()
        chan.on_value_change(chan_change)
        flat.on_value_change(flat_change)
        res.on_value_change(res_change)
        row.on_value_change(lambda _: draw())
        col.on_value_change(lambda _: draw())
        corr.on_value_change(lambda e: (o.update(corrected=bool(e.value)), save_opts(), draw()))
        status.text = f"{h.n_patterns:,} patterns of {h.pattern_width} × {h.pattern_height} px"
        load_or_start()
        draw()

    async def change(e):
        st["key"] = e.value
        await show()
    src.on_value_change(change)
    def legends() -> list[dict]:
        """The scale bar on the image (drag / resize it; exports put it exactly there)."""
        if not sbar.value:
            return []
        return [dict(gs.get("view_legend", default=None) or default_item("scale_bar").model_dump())]

    def on_legends(e):
        items = e.args.get("items", [])
        if items:
            gs.set("view_legend", dict(items[0]))
            if st["viewer"]:
                st["viewer"]._props["legends"] = legends()

    def sbar_change(e):
        gs.set("view_opts", "scale_bar", bool(e.value))
        if st["viewer"]:
            st["viewer"].set(legends=legends() if (st["src"] is None or st["src"]["ps"]) else [])
    sbar.on_value_change(sbar_change)

    async def bar_change(e):
        gs.set("view_opts", "data_bar", bool(e.value))
        await show()
    with_bar.on_value_change(bar_change)

    async def do_export():
        d = st["src"]
        if d is None:
            return

        async def run_step(op, prm, label, backend=None, with_map=False):
            n = ui.notification(f"{label}…", spinner=True, timeout=None)
            try:
                return await ng_run.io_bound(service.run, p, op, prm, backend)
            except Exception as e:  # noqa: BLE001
                error(e, f"{label} failed")
            finally:
                n.dismiss()
        img = d["img"]
        geometry = {"size_px": [img.shape[1], img.shape[0] - (d["strip_px"] if with_bar.value else 0)],
                    "pixel_size_um": d["ps"]}
        if d["kind"] == "mosaic":
            await export_dialog(p, gs, kind="mosaic", run_step=run_step, stitch_step=d["stitch"],
                                detector=d["detector"], sample_id=d["sample_id"], geometry=geometry,
                                legends=legends() if d["ps"] else [])
        else:
            await export_dialog(p, gs, kind="image", run_step=run_step, image=d["file"], sample_id=d["sample_id"],
                                geometry=geometry, legends=legends() if d["ps"] else [])
    do_export = stable(do_export)

    ui.timer(0.05, show, once=True)
