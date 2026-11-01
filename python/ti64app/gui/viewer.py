"""Map viewer page (/viewer): zoom / pan over tiled maps drawn with the active backend's colours, layer panel
(map type, overlays HAGB / LAGB / interface / Burgers / prior-beta GB / crack), IPF key + reference direction,
colour key, annotations and ROIs (drawn on the map, stored as annotations.set steps), crop to a new map,
probe and distance measurement, and the export panel (export.map)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from nicegui import run as ng_run
from nicegui import ui
from nicegui.element import Element

from .. import layers as lay
from .. import service
from ..annotations import ROI_TYPES, Annotation, load_annotations, rgb_of, roi_box
from ..project import Project
from ..settings import IpfKeySetting, load_settings, save_preferences
from . import tiles

IPF_KEYS = ["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"]
DIRECTIONS = ["X", "Y", "Z", "build", "custom"]
TOOLS = {"pan": "pan / zoom", "probe": "probe", "measure": "measure", "text": "text", "arrow": "arrow",
         "line": "line", "polyline": "polyline", "point": "point", "crack": "crack path", "roi_rect": "ROI ▭",
         "roi_poly": "ROI polygon"}


class MapViewer(Element, component="map_viewer.js"):
    """Leaflet map in um (see map_viewer.js)."""

    def __init__(self, base: float, extent: list[float]) -> None:
        super().__init__()
        import nicegui.elements.leaflet as nl  # noqa: PLC0415  (registers the 'nicegui-leaflet' ESM module)

        self.add_resource(Path(nl.__file__).parent / "dist")             # prop 'resource-path' (leaflet.css)
        self._props.update(base=base, extent=extent, layer_url="", layer_opacity=1.0, overlays=[], shapes=[],
                           tool="pan", scale_bar=True)

    def set(self, **props) -> None:
        self._props.update(props)
        self.update()


def shapes_of(items: list[Annotation]) -> list[dict]:
    out = []
    for a in items:
        r = rgb_of(a)
        out.append({"id": a.id, "type": a.type, "xy": [list(p) for p in a.xy], "text": a.text, "width_pt": a.width_pt,
                    "color": "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in r)})
    return out


def viewer_page(current_project, nav, error) -> None:
    nav()
    p: Project | None = current_project()
    if p is None:
        ui.label("Open a project first.").classes("p-4")
        return
    sheets = {s.map_id: s for s in p.samples()}
    if not sheets:
        ui.label("Enter a sample sheet and load a map first.").classes("p-4")
        return
    loaded = [m for m in sheets if lay.available(p, m)["loaded"]]
    st = {"map": (loaded or list(sheets))[0], "layer": None, "view": None, "selected": None}
    ui.add_css("""
      .ti64-pixelated img { image-rendering: pixelated; }
      .ti64-label span { font: 600 13px sans-serif; white-space: nowrap; text-shadow: 0 0 3px #fff; }
      .ti64-tip { font-size: 11px; padding: 0 4px; }
      .ti64-scalebar { background: rgba(255,255,255,.8); padding: 3px 6px 2px; border-radius: 3px; }
      .ti64-scalebar .bar { height: 5px; background: #000; }
      .ti64-scalebar .lab { font: 11px sans-serif; text-align: center; }
    """)
    with ui.row().classes("w-full no-wrap gap-0").style("height: calc(100vh - 64px)"):
        side = ui.column().classes("w-96 h-full overflow-y-auto p-2 gap-1 shrink-0").mark("viewer_side")
        main = ui.column().classes("grow h-full gap-0")
    with main:
        holder = ui.column().classes("w-full grow gap-0")
        status = ui.label("").classes("text-xs text-gray-700 px-2 py-1 w-full").mark("viewer_status")

    viewer: dict[str, MapViewer | None] = {"v": None}

    # ---- side panels -------------------------------------------------------------------------------
    with side:
        with ui.expansion("Map", value=True, icon="map").classes("w-full").mark("panel_map"):
            msel = ui.select(list(sheets), value=st["map"], label="map").classes("w-full").mark("viewer_map")
            minfo = ui.label("").classes("text-xs text-gray-600")

            async def load_map():
                n = ui.notification(f"loading {msel.value} ...", spinner=True, timeout=None)
                try:
                    await ng_run.io_bound(service.run, p, "ebsd.load", {"map_id": msel.value})
                    ui.navigate.to("/viewer")
                except Exception as e:  # noqa: BLE001
                    error(e)
                finally:
                    n.dismiss()
            load_btn = ui.button("Load map", on_click=load_map).props("dense")

        with ui.expansion("Layer", value=True, icon="layers").classes("w-full").mark("panel_layer"):
            kind = ui.select({}, label="map type").classes("w-full").mark("viewer_kind")
            opacity = ui.slider(min=0, max=1, step=0.05, value=1.0).props("label").classes("w-full")
            ui.label("IPF colour key and reference direction (this view; Settings = default)").classes("text-xs")
            s = load_settings().figure
            ipf_w = {}
            for ph in ("Ti (alpha)", "Ti (beta)"):
                k = s.ipf.get(ph) or IpfKeySetting()
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.label(ph.replace("Ti ", "")).classes("w-12 text-xs")
                    ipf_w[ph] = (ui.select(IPF_KEYS, value=k.key).classes("w-32").props("dense").mark(f"ipf_key_{ph[4:-1]}"),
                                 ui.select(DIRECTIONS, value=k.direction).classes("w-24").props("dense").mark(f"ipf_dir_{ph[4:-1]}"))
            with ui.row().classes("items-center gap-1 no-wrap"):
                cv = [ui.number(c, value=v).classes("w-16").props("dense")
                      for c, v in zip("xyz", (s.ipf["Ti (alpha)"].custom_vector or (0.0, 0.0, 1.0)))]
            with ui.row().classes("items-end gap-1"):
                cmin = ui.number("min", value=None).classes("w-20").props("dense")
                cmax = ui.number("max", value=None).classes("w-20").props("dense")
                ui.label("colour range (value maps)").classes("text-xs")
            with ui.row().classes("gap-1"):
                ui.button("Apply", on_click=lambda: show()).props("dense").mark("viewer_apply")

                def save_default():
                    stt = load_settings()
                    for ph, (kk, dd) in ipf_w.items():
                        stt.figure.ipf[ph] = IpfKeySetting(key=kk.value, direction=dd.value,
                                                           custom_vector=tuple(float(c.value or 0) for c in cv)
                                                           if dd.value == "custom" else None,
                                                           key_in_figure=stt.figure.ipf.get(ph, IpfKeySetting()).key_in_figure)
                    ui.notify(f"IPF keys saved as default ({save_preferences(stt)})", type="positive")
                ui.button("Save as default", on_click=save_default).props("dense flat")

        with ui.expansion("Colour key / legend", value=True, icon="palette").classes("w-full").mark("panel_key"):
            keybox = ui.column().classes("w-full gap-1")

        with ui.expansion("Overlays", value=True, icon="border_style").classes("w-full").mark("panel_overlays"):
            ov_box = ui.column().classes("w-full gap-0")
            ov_checks: dict[str, ui.checkbox] = {}
            lw = ui.slider(min=0.5, max=4, step=0.5, value=1.0).props("label").classes("w-full")
            ui.label("overlay line width (screen px)").classes("text-xs")
            sbar = ui.checkbox("scale bar", value=True).mark("viewer_scalebar")

        with ui.expansion("Tools, annotations, ROIs", value=True, icon="edit").classes("w-full").mark("panel_tools"):
            tool = ui.toggle(TOOLS, value="pan").props("dense size=sm").classes("flex-wrap").mark("viewer_tool")
            ui.label("2-point tools: click start and end; polylines / crack / ROI polygon: click points, "
                     "double-click or Enter to finish; Esc cancels.").classes("text-xs text-gray-600")
            with ui.row().classes("items-end gap-1"):
                ann_text = ui.input("text / name").classes("w-32").props("dense").mark("ann_text")
                ann_color = ui.color_input("colour", value="").classes("w-28").props("dense")
                ann_w = ui.number("width pt", value=1.5).classes("w-20").props("dense")
            ann_list = ui.column().classes("w-full gap-0").mark("ann_list")

        with ui.expansion("Export", value=False, icon="download").classes("w-full").mark("panel_export"):
            emode = ui.select({"native": "native (1 px / measurement)", "2": "2× nearest neighbour",
                               "4": "4× nearest neighbour", "8": "8× nearest neighbour",
                               "width": "width in mm at DPI (figure)"}, value="native", label="size").classes("w-full").mark("exp_mode")
            with ui.row().classes("items-end gap-1"):
                ewidth = ui.number("width mm", value=120).classes("w-24").props("dense")
                edpi = ui.number("DPI", value=load_settings().figure.dpi).classes("w-20").props("dense")
                efmt = ui.select(["png", "tiff", "svg", "pdf"], value="png", label="format").classes("w-24").mark("exp_fmt")
            eregion = ui.select({"map": "whole map", "view": "current view"}, value="map", label="region").classes("w-full").mark("exp_region")
            with ui.row().classes("gap-2"):
                e_sb = ui.checkbox("scale bar", value=True)
                e_title = ui.checkbox("title", value=False)
                e_cb = ui.checkbox("colour bar", value=True)
                e_leg = ui.checkbox("legend", value=True)
                e_ann = ui.checkbox("annotations", value=True)
                e_roi = ui.checkbox("ROI outlines", value=False)
            with ui.row().classes("items-end gap-1"):
                ebg = ui.select(["white", "black", "transparent"], value="white", label="background").classes("w-32")
                ename = ui.input("file name").classes("w-40").props("dense")
            ui.label("native / 2× / 4× / 8×: PNG or TIFF, pixel size in the file + sidecar; width: figure by the "
                     "backend renderer (PNG, TIFF, SVG, PDF).").classes("text-xs text-gray-600")
            exp_out = ui.column().classes("w-full gap-0")

            async def do_export():
                params = export_params()
                n = ui.notification(f"exporting ({p.info().backend}) ...", spinner=True, timeout=None)
                try:
                    step = await ng_run.io_bound(service.run, p, "export.map", params)
                    f = p.root / step.outputs["export"].path
                    side_f = p.root / step.outputs["sidecar"].path
                    meta = json.loads(side_f.read_text(encoding="utf-8"))
                    exp_out.clear()
                    with exp_out:
                        ui.label(f"{f.name}: {meta.get('image_px') or ''} px"
                                 + (f", pixel {meta['pixel_size_um'][0]:.4g} × {meta['pixel_size_um'][1]:.4g} µm"
                                    if meta.get("pixel_size_um") else "")).classes("text-xs").mark("exp_result")
                        for w in meta.get("warnings", []):
                            ui.label(w).classes("text-xs text-orange-700")
                        with ui.row():
                            ui.button("file", icon="download", on_click=lambda f=f: ui.download.file(f)).props("dense flat")
                            ui.button("sidecar", icon="data_object", on_click=lambda f=side_f: ui.download.file(f)).props("dense flat")
                        ui.label(str(f.parent)).classes("text-xs text-gray-500 break-all")
                    ui.notify(f"exported {f.name} ({step.id})", type="positive")
                except Exception as e:  # noqa: BLE001
                    error(e)
                finally:
                    n.dismiss()
            ui.button("Export", icon="download", on_click=do_export).props("dense").mark("run_export")

    # ---- helpers -------------------------------------------------------------------------------------
    def layer_params() -> lay.LayerParams:
        d: dict = {"kind": kind.value}
        if kind.value in ("ipf", "parent_ipf"):
            d["ipf_keys"] = {ph: IpfKeySetting(key=k.value, direction=dd.value,
                                               custom_vector=tuple(float(c.value or 0) for c in cv)
                                               if dd.value == "custom" else None).model_dump(mode="json")
                             for ph, (k, dd) in ipf_w.items()}
        if cmin.value is not None and cmax.value is not None:
            d["clim"] = (float(cmin.value), float(cmax.value))
        return lay.LayerParams(**d)

    def export_params() -> dict:
        m = emode.value
        d = {"map_id": st["map"], "layer": layer_params().model_dump(mode="json"),
             "overlays": [k for k, c in ov_checks.items() if c.value], "annotations": e_ann.value,
             "roi_outlines": e_roi.value, "format": efmt.value, "scale_bar": e_sb.value, "title": e_title.value,
             "colorbar": e_cb.value, "legend": e_leg.value, "background": ebg.value, "name": ename.value or ""}
        if m == "native":
            d["mode"] = "native"
        elif m == "width":
            d.update(mode="width", width_mm=float(ewidth.value), dpi=int(edpi.value))
        else:
            d.update(mode="scale", scale=int(m))
        r = eregion.value
        if r == "view" and st["view"]:
            v = st["view"]
            d["roi"] = (v["x0"], v["y0"], v["x1"], v["y1"])
        elif r not in ("map", "view"):
            d["roi_id"] = r
        return d

    def refresh_annotations():
        items = load_annotations(p, st["map"])
        if viewer["v"]:
            viewer["v"].set(shapes=shapes_of(items))
        ann_list.clear()
        rois = {"map": "whole map", "view": "current view"}
        with ann_list:
            if not items:
                ui.label("no annotations").classes("text-xs text-gray-500")
            for a in items:
                with ui.row().classes("items-center gap-1 no-wrap w-full"):
                    ui.label(f"{a.type} {a.text}".strip()).classes("text-xs grow")
                    if a.type in ROI_TYPES:
                        rois[a.id] = f"ROI {a.text or a.id}"
                        b = roi_box(a)
                        ui.button(icon="zoom_in", on_click=lambda b=b: viewer["v"].run_method("zoomTo", *b)).props("dense flat size=xs")
                        ui.button(icon="content_cut", on_click=lambda a=a: crop_dialog(a)).props("dense flat size=xs").tooltip("crop to a new map")
                    ui.button(icon="delete", on_click=lambda a=a: delete_annotation(a.id)).props("dense flat size=xs").mark(f"del_{a.id}")
        eregion.options = rois
        if eregion.value not in rois:
            eregion.value = "map"
        eregion.update()

    async def save_items(items: list[Annotation], label: str):
        try:
            await ng_run.io_bound(service.run, p, "annotations.set",
                                  {"map_id": st["map"], "items": [a.model_dump(mode="json") for a in items]})
            refresh_annotations()
            if any(c.value for k, c in ov_checks.items() if k == "crack"):
                await show_overlays()
            ui.notify(label, type="positive")
        except Exception as e:  # noqa: BLE001
            error(e)

    async def delete_annotation(aid: str):
        await save_items([a for a in load_annotations(p, st["map"]) if a.id != aid], "annotation deleted")

    async def on_shape(e):
        d = e.args
        t, xy = d["type"], [tuple(q) for q in d["xy"]]
        text = ann_text.value or ""
        if t == "text" and not text:
            ui.notify("enter the text first (field 'text / name')", type="warning")
            return
        if t in ROI_TYPES and not text:
            text = f"ROI{1 + sum(a.type in ROI_TYPES for a in load_annotations(p, st['map']))}"
        a = Annotation(map_id=st["map"], type=t, xy=xy, text=text, color=ann_color.value or "",
                       width_pt=float(ann_w.value or 1.5))
        await save_items(load_annotations(p, st["map"]) + [a], f"{t} added")

    def crop_dialog(a: Annotation):
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Crop {st['map']} to ROI {a.text or a.id} ({', '.join(f'{v:.1f}' for v in roi_box(a))} µm)")
            ui.label("Writes maps/<new id>.ang in the project (rows inside the ROI's bounding box) and a sample "
                     "sheet copied from this map.").classes("text-xs")
            nid = ui.input("new map id", value=f"{st['map']}_{a.text or a.id}").classes("w-64")

            async def go():
                try:
                    step = await ng_run.io_bound(service.run, p, "ebsd.crop",
                                                 {"map_id": st["map"], "new_map_id": nid.value, "roi_id": a.id})
                    ui.notify(f"map {nid.value}: {step.metrics.get('n_points')} points; load it from the Map panel",
                              type="positive")
                    dlg.close()
                except Exception as e:  # noqa: BLE001
                    error(e)
            with ui.row():
                ui.button("Crop", on_click=go).mark("crop_go")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def on_probe(e):
        x, y = e.args["xy"][0]
        L = st["layer"].layout
        i = int(L.sample(np.array([x]), np.array([y]))[0])
        if i < 0:
            status.text = f"x {x:.2f} µm, y {y:.2f} µm: no measurement"
            return
        src = st["layer"].info["source"]
        s_ = p.recipe().step(src)
        with np.load(p.root / s_.outputs["ebsd"].path, allow_pickle=False) as z:
            ph = int(z["phase"][i])
            q = z["quat"][i]
            props = {k[5:]: float(z[k][i]) for k in z.files if k.startswith("prop_") and k[5:] in ("ci", "iq", "recon_fit", "parent_grain")}
        c = st["layer"].colors[i]
        names = {0: "not indexed", 1: "α", 2: "β"}
        status.text = (f"x {x:.2f} µm, y {y:.2f} µm | point {i} | {names.get(ph, ph)} | "
                       + " ".join(f"{k} {v:.3g}" for k, v in props.items())
                       + f" | q ({', '.join(f'{v:.4f}' for v in q)}) | colour #{c[0]:02x}{c[1]:02x}{c[2]:02x}")

    def on_measure(e):
        (x0, y0), (x1, y1) = e.args["xy"]
        d = float(np.hypot(x1 - x0, y1 - y0))
        ang = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
        status.text = f"distance {d:.2f} µm, angle from +x toward +y {ang:.1f}°  ({x0:.1f}, {y0:.1f}) → ({x1:.1f}, {y1:.1f})"
        ui.notify(f"{d:.2f} µm", type="info")

    def on_cursor(e):
        st["cursor"] = (e.args["x"], e.args["y"])

    def on_view(e):
        st["view"] = e.args

    def show_key(L: lay.Layer):
        keybox.clear()
        info = L.info
        with keybox:
            ui.label(info.get("title") or "").classes("text-xs font-bold")
            if info.get("colorbar"):
                stops = ", ".join(info["colorbar"][:: max(1, len(info["colorbar"]) // 32)])
                ui.html(f'<div style="height:14px;width:100%;background:linear-gradient(to right,{stops})"></div>').classes("w-full").mark("viewer_colorbar")
                lo, hi = info["clim"]
                with ui.row().classes("w-full justify-between"):
                    ui.label(f"{lo:.3g}").classes("text-xs")
                    ui.label(info.get("colorbar_label") or "").classes("text-xs")
                    ui.label(f"{hi:.3g}").classes("text-xs")
            for lab, col in info.get("legend") or []:
                with ui.row().classes("items-center gap-1"):
                    ui.html(f'<div style="width:14px;height:10px;background:{col}"></div>')
                    ui.label(lab).classes("text-xs")
            if info.get("keys"):
                for ph, k in info["keys"].items():
                    ui.label(f"{ph}: {k['key']} ∥ {k['direction']} {tuple(round(v, 3) for v in k['vector'])}").classes("text-xs")
                ui.button("show IPF key (backend)", on_click=lambda: draw_key()).props("dense flat size=sm").mark("viewer_ipfkey")
            for w in info.get("warnings") or []:
                ui.label(w).classes("text-xs text-orange-700")
            ui.label(f"coloured by {info.get('backend')}; layer {L.key[:8]}").classes("text-xs text-gray-500")

    async def draw_key():
        info = st["layer"].info
        ph = "Ti (beta)" if kind.value == "parent_ipf" else "Ti (alpha)"
        k = (info.get("keys") or {}).get(ph) or next(iter(info["keys"].values()))
        params = {"map_id": st["map"], "kind": "ipf_key", "phases": [ph], "key": k["key"], "direction": k["direction"]}
        if k["direction"] == "custom":
            params["custom_vector"] = k["vector"]
        n = ui.notification("drawing the IPF key ...", spinner=True, timeout=None)
        try:
            step = await ng_run.io_bound(service.run, p, "figure", params)
            with keybox:
                ui.image(str(p.root / step.outputs["figure"].path)).classes("w-full").mark("viewer_keyimg")
        except Exception as e:  # noqa: BLE001
            error(e)
        finally:
            n.dismiss()

    async def show_overlays():
        if st["layer"] is None or viewer["v"] is None:
            return
        ovs = []
        lp = layer_params()
        for name, c in ov_checks.items():
            if not c.value:
                continue
            try:
                O = await ng_run.io_bound(lay.get_overlay, p, st["map"], name, lp)
                ovs.append({"url": tiles.overlay_url(tiles.register_overlay(O, st["layer"].layout), lw.value), "opacity": 1})
            except Exception as e:  # noqa: BLE001
                error(e)
        viewer["v"].set(overlays=ovs)

    async def show():
        if not kind.value:
            return
        n = ui.notification(f"colouring {kind.value} ({p.info().backend}) ...", spinner=True, timeout=None)
        try:
            L = await ng_run.io_bound(lay.get_layer, p, st["map"], layer_params())
        except Exception as e:  # noqa: BLE001
            error(e)
            return
        finally:
            n.dismiss()
        st["layer"] = L
        ext = list(L.layout.extent)
        if viewer["v"] is None or viewer["v"]._props["base"] != L.layout.cw:
            holder.clear()
            with holder:
                v = MapViewer(L.layout.cw, ext).classes("w-full").style("height: calc(100vh - 92px)").mark("map_viewer")
                v.on("shape", on_shape)
                v.on("probe", on_probe)
                v.on("measure", on_measure)
                v.on("view", on_view)
                v.on("shape_click", lambda e: status.set_text(f"annotation {e.args['id']}"))
                viewer["v"] = v
        viewer["v"].set(layer_url=tiles.layer_url(tiles.register_layer(L)), extent=ext, layer_opacity=opacity.value,
                        tool=tool.value, scale_bar=sbar.value)
        show_key(L)
        refresh_annotations()
        await show_overlays()
        status.text = (f"{st['map']}: {L.layout.lut.shape[0]} rows, grid {'hex' if L.layout.hex else 'square'}, "
                       f"step {L.layout.dx:.3g} µm; {L.info.get('title', '')}")

    def select_map(m: str):
        st["map"] = m
        av = lay.available(p, m)
        sh = sheets[m]
        minfo.text = (f"{sh.specimen_id}, {sh.input_state}, BD {tuple(round(c, 3) for c in sh.build_direction.unit_vector)}; "
                      + ("loaded" if av["loaded"] else "not loaded yet"))
        load_btn.set_visibility(not av["loaded"])
        kind.options = {k: lay.LAYER_LABELS[k] for k in av["layers"]}
        kind.value = av["layers"][0] if av["layers"] else None
        kind.update()
        ov_box.clear()
        ov_checks.clear()
        with ov_box:
            for o in av["overlays"]:
                ov_checks[o] = ui.checkbox(lay.OVERLAY_LABELS[o], value=False,
                                           on_change=lambda _: show_overlays()).mark(f"ov_{o}")

    msel.on_value_change(lambda e: (select_map(e.value), show()))
    kind.on_value_change(lambda e: show())
    opacity.on_value_change(lambda e: viewer["v"] and viewer["v"].set(layer_opacity=e.value))
    lw.on_value_change(lambda e: show_overlays())
    sbar.on_value_change(lambda e: viewer["v"] and viewer["v"].set(scale_bar=e.value))
    tool.on_value_change(lambda e: viewer["v"] and viewer["v"].set(tool=e.value))
    select_map(st["map"])
    if lay.available(p, st["map"])["loaded"]:
        ui.timer(0.1, show, once=True)
    else:
        with holder:
            ui.label("Load the map (Map panel) to view it.").classes("p-4")
