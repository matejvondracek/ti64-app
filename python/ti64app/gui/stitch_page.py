"""SEM image stitching page (/stitch, phase 6.5): folder / file picker, detector counts, options, stage-predicted
thumbnail layout, stitch with progress, result with tile borders coloured by match score, nudge / exclude a tile and
re-stitch, export dialog. Everything runs through service.run("sem.stitch", ...) (recorded in the recipe)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from nicegui import run as ng_run
from nicegui import ui

from .. import service
from ..modules import stitching

STITCH_STATE: dict = {"msg": "", "frac": 0.0, "busy": False}


def score_colour(score: float | None, placed_by: str, min_score: float) -> str:
    """Tile border colour: green = strong match, orange = acceptable, red = weak / placed by stage only."""
    if placed_by.startswith("stage") or score is None or (isinstance(score, float) and np.isnan(score)):
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
        x, y = t["x_int_px"] / scale, t["y_int_px"] / scale
        w, h = t["width_px"] / scale, t["height_px"] / scale
        c = score_colour(t["best_score"], t["placed_by"], min_score)
        sw = 4 if t["name"] == selected else 2
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="none" stroke="{c}" '
                   f'stroke-width="{sw}" />')
        label = f'{t["image_number"]} ({t["best_score"] if t["best_score"] is not None else "stage"})'
        out.append(f'<text x="{x + 6:.1f}" y="{y + 18:.1f}" fill="{c}" font-size="14" '
                   f'font-family="sans-serif">{label}</text>')
    return "".join(out)


def layout_thumbnail(tiles: list[stitching.TileInfo], path: Path, max_px: int = 900) -> dict:
    """Thumbnails at the stage-predicted positions (no registration), written as one PNG."""
    from PIL import Image  # noqa: PLC0415

    lay = stitching.layout_preview(tiles)
    s = max(lay["width"], lay["height"]) / max_px if lay["tiles"] else 1.0
    s = max(s, 1.0)
    canvas = np.zeros((int(lay["height"] / s) + 1, int(lay["width"] / s) + 1), np.uint8)
    for t, L in zip(tiles, lay["tiles"]):
        th = stitching.thumbnail(t, max_px=int(max(t.width, t.height) / s) + 1)
        th = np.asarray(Image.fromarray(np.clip(th, 0, 255).astype(np.uint8)).resize(
            (max(1, int(t.width / s)), max(1, int(t.height / s)))))
        y0, x0 = int(L["y"] / s), int(L["x"] / s)
        h, w = min(th.shape[0], canvas.shape[0] - y0), min(th.shape[1], canvas.shape[1] - x0)
        canvas[y0:y0 + h, x0:x0 + w] = th[:h, :w]
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).save(path)
    return {**lay, "scale": s}


def predicted_single_column(tiles: list[stitching.TileInfo]) -> bool:
    lay = stitching.layout_preview(tiles)
    if not lay["tiles"]:
        return False
    xs = [t["x"] for t in lay["tiles"]]
    return (max(xs) - min(xs)) <= 0.05 * tiles[0].width


def _folder_dialog(start: str, on_pick) -> None:
    """Server-side folder / file browser (the app runs on localhost: paths are this machine's)."""
    state = {"dir": Path(start or ".").absolute()}
    if not state["dir"].is_dir():
        state["dir"] = Path(".").absolute()
    with ui.dialog() as dlg, ui.card().classes("w-[44rem]"):
        title = ui.label().classes("font-mono text-sm")
        lst = ui.column().classes("w-full max-h-96 overflow-auto gap-0")
        chosen: set[str] = set()

        def show():
            title.text = str(state["dir"])
            lst.clear()
            chosen.clear()
            with lst:
                if state["dir"].parent != state["dir"]:
                    ui.button(".. (up)", on_click=lambda: go(state["dir"].parent)).props("flat dense align=left")
                for q in sorted(state["dir"].iterdir(), key=lambda q: (not q.is_dir(), q.name.lower())):
                    if q.is_dir():
                        ui.button(f"📁 {q.name}", on_click=lambda q=q: go(q)).props("flat dense align=left no-caps")
                    elif q.suffix.lower() in stitching.IMAGE_SUFFIXES:
                        ui.checkbox(q.name, on_change=lambda e, q=q: (chosen.add(str(q)) if e.value
                                                                     else chosen.discard(str(q))))

        def go(d: Path):
            state["dir"] = d
            show()
        show()
        with ui.row():
            ui.button("Use this folder", on_click=lambda: (on_pick(str(state["dir"]), None), dlg.close())) \
                .mark("pick_folder")
            ui.button("Use selected files", on_click=lambda: (on_pick(None, sorted(chosen)), dlg.close()))
            ui.button("Cancel", on_click=dlg.close).props("flat")
    dlg.open()


def stitch_page(current_project, nav, error) -> None:
    nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-6xl"):
        ui.label("Stitch SEM images").classes("text-xl font-bold")
        if p is None:
            ui.label("Open a project first.")
            return
        src: dict = {"folder": None, "files": None, "tiles": []}
        with ui.row().classes("items-end w-full"):
            folder = ui.input("folder (or pick files)").classes("w-[36rem]").mark("st_folder")

            def picked(f, files):
                src["files"] = files or None
                folder.value = f or (str(Path(files[0]).parent) + f"  [{len(files)} files]" if files else "")
                if f:
                    src["folder"] = f
                scan()
            ui.button("Browse…", on_click=lambda: _folder_dialog(folder.value.split("  [")[0], picked))
            ui.button("Scan", on_click=lambda: scan()).mark("st_scan")
        counts = ui.column().classes("w-full")
        with ui.row().classes("items-end"):
            det = ui.select({"BSE": "BSE", "SE": "SE", "both": "both"}, value="BSE", label="detector") \
                .classes("w-48").mark("st_detector")
            se_bse = ui.checkbox("stitch BSE, apply the layout to SE").mark("st_se_layout")
            strip = ui.select({"none": "none (analysis)", "keep": "keep original (single column)",
                               "generate": "generate new"}, value="none", label="info strip") \
                .classes("w-56").mark("st_strip")
            blend = ui.select({"feather": "linear feathering", "none": "no blending (raw pixels)"},
                              value="feather", label="blending").classes("w-52").mark("st_blend")
            min_score = ui.number("min. NCC", value=0.3, step=0.05).classes("w-28")
        layout_box = ui.column().classes("w-full")
        prog = ui.linear_progress(value=0, show_value=False).classes("w-full").mark("st_progress")
        prog_lab = ui.label("").classes("text-sm")
        result = ui.column().classes("w-full")
        edits: dict = {"exclude": set(), "nudge": {}}

        def scan():
            f = folder.value.split("  [")[0].strip()
            if not src["files"]:
                src["folder"] = f or None
            try:
                tiles = stitching.scan(src["folder"] if not src["files"] else None, src["files"])
            except Exception as e:  # noqa: BLE001
                error(e)
                return
            src["tiles"] = tiles
            c = stitching.detector_counts(tiles)
            names = stitching.detector_detail(tiles)
            det.options = {"BSE": f"BSE ({c.get('BSE', 0)})", "SE": f"SE ({c.get('SE', 0)})",
                           "both": f"both ({c.get('BSE', 0)} + {c.get('SE', 0)}, separate mosaics)"}
            det.update()
            counts.clear()
            with counts:
                ui.label("Detectors: " + ", ".join(f"{k} {v}" for k, v in c.items()) + "   (" +
                         ", ".join(f"{k} {v}" for k, v in names.items()) + ")").mark("st_counts")
                strips = sorted({(t.strip_px, t.strip_source) for t in tiles})
                ui.label("Data bar: " + ", ".join(f"{h} px ({s})" for h, s in strips) +
                         f"; pixel size {sorted({round((t.pixel_size_um or 0) * 1000, 3) for t in tiles})} nm")
            refresh_layout()

        def chosen_tiles():
            want = "SE" if det.value == "SE" else "BSE"
            return [t for t in src["tiles"] if t.detector_class == want]

        def refresh_layout():
            layout_box.clear()
            tl = chosen_tiles()
            if not tl:
                return
            single = predicted_single_column(tl)
            strip.options = {"none": "none (analysis)",
                             "keep": "keep original (single column)" + ("" if single else " – not a single column"),
                             "generate": "generate new"}
            strip.update()
            try:
                key = abs(hash(tuple(t.path for t in tl))) % 10**10
                png = p.cache_dir / f"stitch_layout_{key}.png"
                lay = layout_thumbnail(tl, png)
            except Exception as e:  # noqa: BLE001
                error(e)
                return
            with layout_box:
                ui.label(f"Layout from the stage ({lay['source']}, before registration): {len(tl)} tiles").classes(
                    "text-sm")
                svg = "".join(
                    f'<rect x="{L["x"] / lay["scale"]:.1f}" y="{L["y"] / lay["scale"]:.1f}" '
                    f'width="{L["w"] / lay["scale"]:.1f}" height="{L["h"] / lay["scale"]:.1f}" fill="none" '
                    f'stroke="#1f77b4" stroke-width="1"/><text x="{L["x"] / lay["scale"] + 4:.1f}" '
                    f'y="{L["y"] / lay["scale"] + 14:.1f}" fill="#1f77b4" font-size="12">{L["image_number"]}</text>'
                    for L in lay["tiles"])
                ui.interactive_image(str(png), content=svg).classes("max-w-xl") \
                    .mark("st_layout")
        det.on_value_change(lambda _: refresh_layout())

        def tick():
            prog.value = STITCH_STATE["frac"]
            prog_lab.text = STITCH_STATE["msg"] if STITCH_STATE["busy"] else prog_lab.text
        ui.timer(0.3, tick)

        def params() -> dict:
            pr = {"detector": det.value, "se_layout": "bse" if se_bse.value else "own", "info_strip": strip.value,
                  "blend": blend.value, "min_score": float(min_score.value or 0.3),
                  "exclude": sorted(edits["exclude"]), "nudge": {k: list(v) for k, v in edits["nudge"].items()}}
            if src["files"]:
                pr["files"] = src["files"]
            else:
                pr["folder"] = src["folder"] or folder.value.strip()
            return pr

        async def do_stitch():
            if STITCH_STATE["busy"]:
                return
            STITCH_STATE.update(busy=True, frac=0.0, msg="starting")
            stitching.PROGRESS["hook"] = lambda m, f: STITCH_STATE.update(msg=m, frac=f)
            try:
                step = await ng_run.io_bound(service.run, p, "sem.stitch", params())
                ui.notify(f"stitched ({step.id}, {step.metrics.get('seconds')} s)", type="positive")
                show_result(step)
            except Exception as e:  # noqa: BLE001
                error(e)
            finally:
                stitching.PROGRESS["hook"] = None
                STITCH_STATE.update(busy=False, frac=1.0)
                prog_lab.text = ""
        with ui.row():
            ui.button("Stitch", on_click=do_stitch).mark("st_run")

        def show_result(step):
            result.clear()
            with result:
                for d in ("BSE", "SE"):
                    if f"sidecar_{d}" not in step.outputs:
                        continue
                    side = json.loads((p.root / step.outputs[f"sidecar_{d}"].path).read_text(encoding="utf-8"))
                    prev = p.root / step.outputs[f"preview_{d}"].path
                    sc = side.get("preview_scale", 1.0)
                    w, h = side["mosaic_px"]
                    ui.label(f"{d}: {side['n_tiles']} tiles → {w} × {h} px "
                             f"({side['pixel_size_um'] * 1000:.2f} nm/px), residual max "
                             f"{side['residual_px']['max']} px; placed by stage only: "
                             f"{side['stage_only_tiles'] or 'none'}; strip: {side['strip']['mode']}") \
                        .mark(f"st_summary_{d}")
                    sel = {"name": None}
                    img = ui.interactive_image(str(prev), content=tile_svg(side, sc, None, side["params"]
                                                                                        ["min_score"]),
                                               events=["click"]).classes("max-w-md").mark(f"st_result_{d}")
                    panel = ui.row().classes("items-end")

                    def click(e, side=side, img=img, panel=panel, sel=sel, sc=sc):
                        x, y = e.image_x * sc, e.image_y * sc
                        hit = [t for t in side["tiles"] if t["x_int_px"] <= x < t["x_int_px"] + t["width_px"]
                               and t["y_int_px"] <= y < t["y_int_px"] + t["height_px"]]
                        if not hit:
                            return
                        t = hit[-1]
                        sel["name"] = t["name"]
                        img.content = tile_svg(side, sc, t["name"], side["params"]["min_score"])
                        tile_panel(panel, t)
                    img.on_mouse(click)
                    with ui.row():
                        ui.button(f"Export {d}…", on_click=lambda step=step, d=d: export_dialog(step, d)) \
                            .mark(f"st_export_{d}")
                    with ui.expansion(f"{d} pairs").classes("w-full"):
                        cols = ["a", "b", "offset_px", "score", "ok", "used", "residual_px", "reason"]
                        ui.table(columns=[{"name": c, "label": c, "field": c, "align": "left"} for c in cols],
                                 rows=[{c: str(q[c]) for c in cols} for q in side["pairs"]]).props("dense")

        def tile_panel(panel, t):
            panel.clear()
            key = str(t["image_number"]) if t["image_number"] not in (None, -1) else t["name"]
            nx, ny = edits["nudge"].get(key, (0.0, 0.0))
            with panel:
                ui.label(f"{t['name']}: x {t['x_px']} px, y {t['y_px']} px, score {t['best_score']}, "
                         f"{t['placed_by']}").classes("text-sm")
                dx = ui.number("nudge dx [px]", value=nx).classes("w-32").mark("st_nudge_dx")
                dy = ui.number("nudge dy [px]", value=ny).classes("w-32").mark("st_nudge_dy")
                ex = ui.checkbox("exclude", value=key in edits["exclude"]).mark("st_exclude")

                async def apply():
                    if dx.value or dy.value:
                        edits["nudge"][key] = (float(dx.value or 0), float(dy.value or 0))
                    else:
                        edits["nudge"].pop(key, None)
                    (edits["exclude"].add if ex.value else edits["exclude"].discard)(key)
                    await do_stitch()
                ui.button("Re-stitch", on_click=apply).mark("st_restitch")

        def export_dialog(step, d):
            with ui.dialog() as dlg, ui.card().classes("w-[32rem]"):
                ui.label(f"Export the {d} mosaic").classes("font-bold")
                dest = ui.input("destination folder", value=str(Path.home() / "Documents")).classes("w-full") \
                    .mark("st_export_dest")
                files = {"TIFF (mosaic)": f"mosaic_{d}", "PNG preview": f"preview_{d}",
                         "JSON sidecar": f"sidecar_{d}", "CSV tile table": f"tiles_{d}"}
                boxes = {k: ui.checkbox(k, value=True) for k in files}

                def go():
                    out = Path(dest.value)
                    out.mkdir(parents=True, exist_ok=True)
                    n = 0
                    for k, cb in boxes.items():
                        if cb.value and files[k] in step.outputs:
                            srcf = p.root / step.outputs[files[k]].path
                            shutil.copy2(srcf, out / srcf.name)
                            n += 1
                    ui.notify(f"{n} files copied to {out}", type="positive")
                    dlg.close()
                with ui.row():
                    ui.button("Copy", on_click=go).mark("st_export_go")
                    ui.button("Cancel", on_click=dlg.close).props("flat")
            dlg.open()

        last = service.latest_step(p, ("sem.stitch",))
        if last:
            src["folder"] = last.params.get("folder")
            src["files"] = last.params.get("files")
            folder.value = src["folder"] or ""
            show_result(last)
