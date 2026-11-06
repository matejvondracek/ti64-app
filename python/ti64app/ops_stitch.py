"""Service operation sem.stitch (phase 6.5): stitch overlapping SEM images into one mosaic per detector.

Python-only (modules/stitching.py), works with either backend. Outputs per detector DET (BSE / SE):
  mosaic_DET   TIFF (BigTIFF + pyramid when large), strip-free pixels + the optional info strip below
  preview_DET  PNG preview
  sidecar_DET  JSON: inputs, detector, tile positions (px, um), pixel size, stage -> mosaic transform, scores,
               residuals, info-strip height
  tiles_DET    CSV of the tile table
The mosaic + its stage transform feed phase 7 (map positions) and phase 8 (crack images, registration to the IQ map).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .legends import LegendItem
from .modules import stitching
from .service import OpContext, op


class StitchOpParams(stitching.StitchParams):
    sample_id: str | None = None              # sample the images belong to (output names start with it)


def _resolve(p: stitching.StitchParams) -> stitching.StitchParams:
    if p.folder:
        p.folder = str(Path(p.folder).absolute())
    if p.files:
        p.files = [str(Path(f).absolute()) for f in p.files]
    if not p.folder and not p.files:
        raise ValueError("give a folder or a list of image files")
    if p.normalize is None:                       # record the resolved value
        p.normalize = p.blend != "none"
    return p


def selected_tiles(p: stitching.StitchParams, tiles: list[stitching.TileInfo]) -> list[stitching.TileInfo]:
    """Tiles the op will read: the chosen detector(s) (+ BSE when SE uses the BSE layout), minus exclusions."""
    want = {"BSE", "SE"} if p.detector == "both" else {p.detector}
    if "SE" in want and p.se_layout == "bse":
        want.add("BSE")
    return [t for t in tiles if t.detector_class in want and not stitching._match(t, p.exclude)]


@op("sem.stitch", StitchOpParams, uses_backend=False)
def _sem_stitch(ctx: OpContext) -> None:
    """Stitch overlapping SEM images (JEOL BSE / SE) of a folder or file list into one mosaic per detector."""
    p = _resolve(stitching.StitchParams.model_validate(ctx.params))
    tiles = stitching.scan(p.folder, p.files, strip_px=p.strip_px)
    for t in selected_tiles(p, tiles):
        ctx.add_input(t.path)
        if t.txt:
            ctx.add_input(t.txt)
    res = stitching.stitch(p, ctx.out_dir, tiles=tiles)
    ctx.params = p.model_dump(mode="json")
    counts = stitching.detector_counts(tiles)
    sid = ctx.params.get("sample_id") or ctx.step.sample_id
    ctx.params["sample_id"] = sid
    pre = f"{sid}_" if sid else ""
    for det, r in res.items():
        ctx.output(f"mosaic_{det}", "mosaic", r.tiff, f"{pre}mosaic_{det}")
        ctx.output(f"preview_{det}", "png", r.preview, f"{pre}mosaic_{det}_preview")
        ctx.output(f"sidecar_{det}", "json", r.info, f"{pre}mosaic_{det}")
        ctx.output(f"tiles_{det}", "csv", r.table, f"{pre}tiles_{det}")
        ctx.metrics.update({f"{det}_tiles": r.info["n_tiles"], f"{det}_width_px": r.info["mosaic_px"][0],
                            f"{det}_height_px": r.info["mosaic_px"][1],
                            f"{det}_residual_max_px": r.info["residual_px"]["max"] or 0.0,
                            f"{det}_stage_only": len(r.info["stage_only_tiles"])})
    ctx.metrics.update({f"count_{k}": v for k, v in counts.items()})


# ---- export of a stitched mosaic (GUI Export dialog, phase 6.6) ----------------------------------------------
class MosaicExportParams(BaseModel):
    stitch: str | None = None                         # sem.stitch step id, or:
    image: str | None = None                          # a single SEM image (linked file; View tab)
    detector: Literal["BSE", "SE"] = "BSE"
    format: Literal["png", "tiff", "svg", "pdf"] = "png"
    mode: Literal["native", "width", "scale"] = "native"   # native = 1 image pixel per file pixel
    scale: float = 1.0                                # mode 'scale': output pixels per image pixel (e.g. 0.5, 2)
    width_mm: float = 120.0
    dpi: int = 600
    scale_bar: bool = True                            # (without legends) scale bar bottom right
    info_strip: bool = True                           # keep the mosaic's info strip / the image's data bar
    legends: list[LegendItem] | None = None           # GUI: scale bar where the viewer shows it (WYSIWYG)
    name: str = ""
    sample_id: str | None = None


@op("sem.export", MosaicExportParams, uses_backend=False)
def _sem_export(ctx: OpContext) -> None:
    """Export a stitched mosaic (stitch = sem.stitch step) or one SEM image (image = linked file) as PNG / TIFF /
    SVG / PDF: native pixels, a scale factor or a width in mm at a DPI, optional scale bar (from the pixel size;
    with `legends` exactly where the viewer shows it) and the info strip / data bar. A sidecar JSON records the
    pixel size of the exported image."""
    import base64  # noqa: PLC0415
    import io  # noqa: PLC0415
    import json  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import tifffile  # noqa: PLC0415
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from .modules.jeol_strip import _font, bar_label, nice_bar  # noqa: PLC0415

    p = MosaicExportParams.model_validate(ctx.params)
    det = p.detector
    if p.stitch:
        st = ctx.project.recipe().step(p.stitch)
        if f"mosaic_{det}" not in st.outputs:
            raise ValueError(f"step {p.stitch} has no {det} mosaic")
        ctx.step.inputs.append(f"{p.stitch}:mosaic_{det}")
        side = json.loads((ctx.project.root / st.outputs[f"sidecar_{det}"].path).read_text(encoding="utf-8"))
        img = tifffile.imread(ctx.project.root / st.outputs[f"mosaic_{det}"].path)
        h_an = int(side["mosaic_px"][1])
        source = f"{p.stitch}:mosaic_{det}"
    elif p.image:
        from .io.bse import read_image  # noqa: PLC0415

        t = stitching.read_tile(p.image)
        ctx.add_input(t.path)
        if t.txt:
            ctx.add_input(t.txt)
        img = read_image(t.path)
        h_an = t.height
        side = {"pixel_size_um": t.pixel_size_um}
        det = t.detector_class if t.detector_class in ("BSE", "SE") else det
        source = str(Path(t.path))
    else:
        raise ValueError("give stitch (a sem.stitch step) or image (a linked SEM image)")
    if img.ndim > 2:
        img = img[..., 0]
    strip = img[h_an:] if img.shape[0] > h_an else None
    im = img[:h_an]
    if im.dtype != np.uint8:
        lo, hi = np.percentile(im, (0.1, 99.9))
        im = np.clip((im.astype(np.float32) - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
    ps = side.get("pixel_size_um")
    pil = Image.fromarray(im)
    scale = 1.0
    if p.mode == "width":
        w_px = max(1, int(round(p.width_mm / 25.4 * p.dpi)))
        scale = w_px / pil.width
        pil = pil.resize((w_px, max(1, int(round(pil.height * scale)))), Image.Resampling.LANCZOS)
    elif p.mode == "scale" and abs(p.scale - 1.0) > 1e-9:
        scale = float(p.scale)
        pil = pil.resize((max(1, int(round(pil.width * scale))), max(1, int(round(pil.height * scale)))),
                         Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.NEAREST)
    out_ps = ps / scale if ps else None
    if p.legends is not None:
        from .legends import compose  # noqa: PLC0415

        pil = compose(pil, p.legends, out_ps, {})
    elif p.scale_bar and out_ps:
        L, bar = nice_bar(out_ps, 0.2 * pil.width)
        cap = max(10, pil.width // 70)
        font = _font(cap)
        d = ImageDraw.Draw(pil)
        x1, y1 = pil.width - cap * 2, pil.height - cap * 2
        x0 = x1 - bar
        d.rectangle([x0 - cap, y1 - cap * 3.2, x1 + cap, y1 + cap], fill=255)
        d.rectangle([x0, y1 - cap * 0.5, x1, y1], fill=0)
        d.text(((x0 + x1) / 2, y1 - cap * 0.9), bar_label(L), fill=0, font=font, anchor="ms")
    if p.info_strip and strip is not None and strip.size:
        s = Image.fromarray(strip.astype(np.uint8) if strip.dtype == np.uint8 else
                            np.clip(strip, 0, 255).astype(np.uint8))
        if s.width != pil.width:
            s = s.resize((pil.width, max(1, int(round(s.height * pil.width / s.width)))), Image.Resampling.LANCZOS)
        both = Image.new("L", (pil.width, pil.height + s.height))
        both.paste(pil, (0, 0))
        both.paste(s, (0, pil.height))
        pil = both
    sid = ctx.step.sample_id or side.get("sample_id")
    from .ops_export import sample_prefixed  # noqa: PLC0415

    stem = p.name or sample_prefixed(sid, f"mosaic_{det}" if p.stitch else Path(p.image).stem)
    ext = {"png": ".png", "tiff": ".tif", "svg": ".svg", "pdf": ".pdf"}[p.format]
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    out = ctx.out_dir / f"{stem}{ext}"
    dpi = (p.dpi, p.dpi) if p.mode == "width" else ((25400 / out_ps,) * 2 if out_ps else (300, 300))
    if p.format == "png":
        pil.save(out, dpi=dpi)
    elif p.format == "tiff":
        pil.save(out, compression="tiff_deflate", dpi=dpi)
    elif p.format == "pdf":
        pil.convert("RGB").save(out, "PDF", resolution=float(dpi[0]))
    else:
        buf = io.BytesIO()
        pil.save(buf, "PNG")
        wmm = pil.width / dpi[0] * 25.4
        hmm = pil.height / dpi[0] * 25.4
        out.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                       f'width="{wmm:.3f}mm" height="{hmm:.3f}mm" viewBox="0 0 {pil.width} {pil.height}">'
                       f'<image width="{pil.width}" height="{pil.height}" xlink:href="data:image/png;base64,'
                       f'{base64.b64encode(buf.getvalue()).decode()}"/></svg>', encoding="utf-8")
    ctx.output("export", p.format, out, stem)
    ctx.output("sidecar", "json", {"source": source, "detector": det, "format": p.format,
                                   "image_px": [pil.width, pil.height], "pixel_size_um": out_ps,
                                   "info_strip": bool(p.info_strip and strip is not None),
                                   "scale_bar": p.scale_bar if p.legends is None else
                                   any(it.kind == "scale_bar" for it in p.legends),
                                   "legends": [it.model_dump() for it in p.legends or []]}, stem)
