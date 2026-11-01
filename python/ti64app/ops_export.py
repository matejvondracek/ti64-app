"""Phase 5 operations: annotations / ROIs, map crop, map export (pixel-exact or figure) with a JSON sidecar.

export.map modes (decisions D15):
  native / scale   pixel-exact PNG or TIFF: one measurement = scale x scale pixels (1 = native), nearest
                   neighbour. Colours from the backend (measurement_colors: IPF key, colour map), pixel layout and
                   overlay lines from raster.py (same geometry for both backends). Pixel size in the resolution
                   tags and the sidecar; texts and the scale bar are not burnt in (no font renderer).
  width            a figure exactly width_mm wide at dpi (PNG / TIFF / SVG / PDF), drawn entirely by the
                   backend renderer (pymtex: matplotlib; MATLAB: print), overlays and annotations as vectors,
                   optional scale bar, title, colour bar, legend.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from . import __version__
from .annotations import (Annotation, find, load_annotations, rasterize_primitives, roi_box,
                          save_map_annotations, to_primitives, validate)
from .hashing import bytes_hash, file_fingerprint
from .layers import RECON_KINDS, LayerParams, layer_spec, layout_for
from .ops_ebsd import overlay_segments
from .raster import colour_image, exact_grid, rasterize_segments, write_raster
from .service import OpContext, op
from .settings import load_settings

OverlayName = Literal["hagb", "lagb", "interface", "burgers", "beta_gb", "crack"]


# ---- annotations -------------------------------------------------------------------------------------
class AnnotationsSetParams(BaseModel):
    map_id: str
    items: list[Annotation] = Field(default_factory=list)


@op("annotations.set", AnnotationsSetParams, uses_backend=False)
def _annotations_set(ctx: OpContext) -> None:
    """Replace the annotations and ROIs of one map (text, arrow, line, polyline, point, crack, roi_rect,
    roi_poly; coordinates in um). Undo restores the previous set."""
    p = AnnotationsSetParams.model_validate(ctx.params)
    ctx.project.sample(p.map_id)                          # map must exist
    ids = [a.id for a in p.items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate annotation ids")
    for a in p.items:
        a.map_id = p.map_id
        validate(a)
    save_map_annotations(ctx.project, p.map_id, p.items)
    ctx.params.update(items=[a.model_dump(mode="json") for a in p.items])
    ctx.metrics.update(n_items=len(p.items))
    ctx.output("annotations", "json", {"map_id": p.map_id, "items": [a.model_dump(mode="json") for a in p.items]})


# ---- crop ---------------------------------------------------------------------------------------------
class CropParams(BaseModel):
    map_id: str
    new_map_id: str
    roi: tuple[float, float, float, float] | None = None     # x0, y0, x1, y1 (um)
    roi_id: str | None = None                                # or an ROI annotation (id or name)


def crop_ang(src: Path, dst: Path, roi: tuple[float, float, float, float]) -> dict:
    """Cut the measurements inside `roi` from a TSL .ang (text rows copied unchanged, coordinates kept).
    Hex grids start on an OIM 'odd' row (even 0-based row) so the row shift is unchanged; both row kinds get
    the same column range, so NCOLS_ODD = NCOLS_EVEN. Streams the file (no full load)."""
    from .io.ang import read_header  # noqa: PLC0415

    h = read_header(src)
    n_odd, n_even, n_rows = (int(h.fields[k]) for k in ("NCOLS_ODD", "NCOLS_EVEN", "NROWS"))
    dx = float(h.fields["XSTEP"])
    dy = float(h.fields["YSTEP"])
    hexg = h.is_hex
    x0r, y0r, x1r, y1r = roi
    x0r, x1r = sorted((x0r, x1r))
    y0r, y1r = sorted((y0r, y1r))
    with open(src, "r", encoding="latin-1") as fh:
        header = []
        line = fh.readline()
        while line.startswith("#"):
            header.append(line)
            line = fh.readline()
        first = line.split()
        xs, ys = float(first[3]), float(first[4])
        r0 = max(0, math.ceil((y0r - ys) / dy - 1e-6))
        if hexg:
            r0 -= r0 % 2
        r1 = min(n_rows - 1, math.floor((y1r - ys) / dy + 1e-6))
        c0 = max(0, math.ceil((x0r - xs) / dx - 1e-6))
        c1 = min((min(n_odd, n_even) if hexg else n_odd) - 1, math.floor((x1r - xs) / dx + 1e-6))
        if r1 < r0 or c1 < c0:
            raise ValueError(f"ROI {roi} contains no measurement rows / columns")
        out_rows: list[str] = []
        r = 0
        pending = [line]
        while r <= r1:
            n = (n_odd if r % 2 == 0 else n_even) if hexg else n_odd
            row = pending + [fh.readline() for _ in range(n - len(pending))]
            pending = []
            if r >= r0:
                out_rows.extend(row[c0:c1 + 1])
            r += 1
    nc, nr = c1 - c0 + 1, r1 - r0 + 1
    hdr = "".join(header)
    for k, v in (("NCOLS_ODD", nc), ("NCOLS_EVEN", nc if hexg else nc), ("NROWS", nr)):
        hdr = re.sub(rf"(#\s*{k}:\s*)\d+", rf"\g<1>{v}", hdr)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="latin-1", newline="") as fh:
        fh.write(hdr)
        fh.writelines(out_rows)
    return {"rows": [r0, r1 + 1], "cols": [c0, c1 + 1], "n_points": len(out_rows), "n_rows": nr, "n_cols": nc,
            "x_um": [xs + c0 * dx, xs + c1 * dx], "y_um": [ys + r0 * dy, ys + r1 * dy], "grid": "hex" if hexg else "square"}


@op("ebsd.crop", CropParams, uses_backend=False)
def _ebsd_crop(ctx: OpContext) -> None:
    """Crop a map to an ROI: writes maps/<new_map_id>.ang in the project (the .ang rows inside the ROI, original
    coordinates, hex row parity kept) and a sample sheet for the new map copied from the parent (same build
    direction, input state, 4PB geometry). Load the new map like any other map; both backends read the same file."""
    p = CropParams.model_validate(ctx.params)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", p.new_map_id):
        raise ValueError("new_map_id: letters, digits, _ . - only")
    if p.roi is None and p.roi_id is None:
        raise ValueError("give roi (x0, y0, x1, y1 in um) or roi_id")
    roi = p.roi or roi_box(find(ctx.project, p.roi_id))
    parent = ctx.project.sample(p.map_id)
    if (ctx.project.samples_dir / f"{p.new_map_id}.json").is_file():
        raise FileExistsError(f"map {p.new_map_id} already exists")
    src = Path(parent.map_file)
    ctx.add_input(src)
    dst = ctx.project.root / "maps" / f"{p.new_map_id}.ang"
    info = crop_ang(src, dst, roi)
    sheet = parent.model_copy(update={"map_id": p.new_map_id, "map_file": str(dst),
                                      "notes": (parent.notes + "\n" if parent.notes else "")
                                      + f"crop of {p.map_id}: x {info['x_um']}, y {info['y_um']} um"})
    from .project import write_json  # noqa: PLC0415

    write_json(ctx.project.samples_dir / f"{p.new_map_id}.json", sheet.model_dump(mode="json"))
    ctx.params.update(roi=list(roi))
    ctx.metrics.update(n_points=info["n_points"])
    ctx.output("crop", "json", {**info, "parent": p.map_id, "map_file": dst.relative_to(ctx.project.root).as_posix(),
                                "ang_hash": bytes_hash(dst),
                                "sample": {**sheet.model_dump(mode="json"), "map_file": f"maps/{p.new_map_id}.ang"}})


# ---- export --------------------------------------------------------------------------------------------
class ExportParams(BaseModel):
    map_id: str
    layer: LayerParams = Field(default_factory=LayerParams)
    overlays: list[OverlayName] = Field(default_factory=list)
    annotations: bool = True                  # draw the map's annotations (texts only in width mode)
    roi_outlines: bool = False                # draw ROI outlines too
    roi: tuple[float, float, float, float] | None = None     # x0, y0, x1, y1 (um)
    roi_id: str | None = None                 # or an ROI annotation (id or name)
    mode: Literal["native", "scale", "width"] = "native"
    scale: Literal[2, 4, 8] = 2               # mode 'scale': pixels per measurement (nearest neighbour)
    width_mm: float = 120.0                   # mode 'width'
    dpi: int = 600                            # mode 'width'
    format: Literal["png", "tiff", "svg", "pdf"] = "png"
    scale_bar: bool = True                    # width mode
    title: bool = False                       # width mode
    colorbar: bool = True                     # width mode (value maps)
    legend: bool = True                       # width mode (phases, overlays, IDs)
    line_px: float | None = None              # pixel-exact: overlay line width in pixels (default max(1, scale/2))
    background: Literal["white", "black", "transparent"] = "white"
    name: str = ""                            # file name stem (default <map>_<kind>)


BACKGROUNDS = {"white": (255, 255, 255, 255), "black": (0, 0, 0, 255), "transparent": (0, 0, 0, 0)}


@op("export.map", ExportParams, uses_backend=True)
def _export_map(ctx: OpContext) -> None:
    """Export a map layer with overlays and annotations: pixel-exact (native = one pixel per measurement, or 2x /
    4x / 8x nearest neighbour; PNG / TIFF) or as a figure of a given width in mm at a DPI (PNG / TIFF / SVG / PDF,
    drawn by the active backend). Optional ROI. Writes the file and a JSON sidecar with every parameter."""
    p = ExportParams.model_validate(ctx.params)
    style = load_settings().figure
    sheet = ctx.project.sample(p.map_id)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", p.name or f"{p.map_id}_{p.layer.kind}")
    roi = tuple(p.roi) if p.roi else (roi_box(find(ctx.project, p.roi_id)) if p.roi_id else None)
    if p.mode != "width" and p.format in ("svg", "pdf"):
        raise ValueError("SVG / PDF are figures: use mode 'width' (pixel-exact exports are PNG or TIFF)")
    spec, handle, meta, src = layer_spec(ctx, p.map_id, p.layer)
    L = layout_for(ctx.project, p.map_id, src)
    ov = overlay_segments(ctx, p.map_id, list(p.overlays), p.layer.grains, p.layer.recon, style.boundary_line_width) \
        if p.overlays else None
    items = [a for a in load_annotations(ctx.project, p.map_id) if a.type != "crack"] if p.annotations else []
    x0, x1, y0, y1 = L.extent
    view_w = (abs(roi[2] - roi[0]) if roi else x1 - x0)
    prims = to_primitives(items, view_w, include_roi=p.roi_outlines)
    # the resolved steps (recorded in the params, so replay draws exactly the same data)
    lay = p.layer.model_dump(mode="json")
    lay["recon" if p.layer.kind in RECON_KINDS else "source"] = src
    if ov:
        lay.update({k: v for k, v in ov["used"].items() if k in ("grains", "recon")})
    if p.layer.kind in ("kam", "grod", "gos", "gnd") and ctx.params.get("values"):
        lay["values"] = ctx.params.pop("values")
    side: dict[str, Any] = {
        "map_id": p.map_id, "map_file": sheet.map_file, "map_fingerprint": file_fingerprint(sheet.map_file),
        "input_state": sheet.input_state, "build_direction": sheet.build_direction.model_dump(mode="json"),
        "layer": {**lay, "title": spec.title, "colorbar_label": spec.colorbar_label,
                  "ipf_keys": meta.get("keys")},
        "source_steps": {"map": src, **({k: v for k, v in ov["used"].items()} if ov else {})},
        "overlays": list(p.overlays), "boundary_length_um": ov["lengths"] if ov else {},
        "annotations": [a.model_dump(mode="json") for a in items],
        "roi_um": list(roi) if roi else None, "grid": "hex" if L.hex else "square",
        "step_um": [L.dx, L.dy], "mode": p.mode, "format": p.format,
        "backend": ctx.backend.name, "versions": ctx.step.versions, "app_version": __version__,
        "recipe_step": ctx.step.id, "figure_style": style.model_dump(mode="json"), "warnings": list(meta.get("warnings", []))}
    ext = {"png": ".png", "tiff": ".tif", "svg": ".svg", "pdf": ".pdf"}[p.format]
    out = ctx.out_dir / f"{stem}{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    if p.mode in ("native", "scale"):
        k = 1 if p.mode == "native" else int(p.scale)
        g = exact_grid(L, k, roi)
        colors, cinfo = ctx.backend.measurement_colors(spec)
        img = colour_image(L, g, colors, BACKGROUNDS[p.background])
        line_px = p.line_px if p.line_px is not None else max(1.0, k / 2)
        if ov:
            for seg, rgb, w in ov["parts"]:
                a, b = g.to_px(seg["x1"], seg["y1"])
                c, d = g.to_px(seg["x2"], seg["y2"])
                rasterize_segments(img, a, b, c, d, rgb, line_px * w / style.boundary_line_width)
        skipped = rasterize_primitives(img, g, prims, line_px)
        if skipped:
            side["warnings"].append(f"texts not drawn in pixel-exact exports (use mode 'width'): {skipped}")
        side.update(scale=k, image_px=[g.W, g.H], pixel_size_um=[g.px, g.py], origin_um=[g.x0, g.y0],
                    physical_size_um=[g.W * g.px, g.H * g.py], line_px=line_px, background=p.background,
                    colour={"cmap": cinfo.get("cmap"), "clim": cinfo.get("clim")},
                    notes=g.notes + ["colours per measurement from the backend; pixel layout and lines from "
                                     "ti64app.raster (decisions D15)", "no scale bar or texts: see pixel_size_um"])
        write_raster(out, img, (g.px, g.py), json.dumps({"ti64app": side["recipe_step"], "map_id": p.map_id,
                                                         "layer": p.layer.kind, "pixel_size_um": [g.px, g.py]}),
                     transparent=p.background == "transparent")
        ctx.output("export", p.format, out, stem)
    else:
        spec.exact = True
        spec.width_cm = p.width_mm / 10.0
        spec.dpi = int(p.dpi)
        spec.scale_bar = p.scale_bar
        if not p.title:
            spec.title = ""
        if not p.colorbar:
            spec.colorbar_label = ""
        elif spec.values is not None and not spec.colorbar_label:
            spec.colorbar_label = " "
        if ov:
            spec.seg, spec.seg_rgb = ov["seg"], ov["rgb"]
            spec.seg_widths = ov["widths"] if np.unique(ov["widths"]).size > 1 else None
            spec.legend = spec.legend + ov["legend"]
        if not p.legend:
            spec.legend = []
        spec.prims = prims
        if roi:
            spec.xlim, spec.ylim = (roi[0], roi[2]), (roi[1], roi[3])
        ctx.backend.render(handle(), spec, out)
        side.update(width_mm=p.width_mm, dpi=p.dpi, expected_width_px=round(p.width_mm / 25.4 * p.dpi)
                    if p.format in ("png", "tiff") else None, scale_bar=p.scale_bar, title=p.title,
                    colorbar=p.colorbar, legend=p.legend, notes=[f"figure drawn by the {ctx.backend.name} renderer"])
        if p.format in ("png", "tiff"):
            from PIL import Image  # noqa: PLC0415

            with Image.open(out) as im:
                side["image_px"] = list(im.size)
        ctx.output("export", p.format, out, stem)
    side["file"] = out.name
    ctx.params.update(roi=list(roi) if roi else None, roi_id=None if roi else p.roi_id, layer=lay)
    for k in ("source", "grains", "recon"):              # set on ctx.params by figure_spec / overlay_segments
        ctx.params.pop(k, None)
    ctx.metrics.update(n_overlay_segments=int(len(ov["rgb"])) if ov else 0)
    ctx.output("sidecar", "json", side, stem)


def export_files(project, step) -> tuple[Path, Path]:
    """(exported file, sidecar) of an export.map step."""
    return project.root / step.outputs["export"].path, project.root / step.outputs["sidecar"].path
