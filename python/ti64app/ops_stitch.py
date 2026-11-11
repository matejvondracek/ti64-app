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

from pydantic import BaseModel, Field

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
        if r.seams is not None and Path(r.seams).is_file():     # seam lines for the Result viewer (fix 3.16)
            import numpy as np  # noqa: PLC0415

            with np.load(r.seams) as z:
                seams = {k: z[k] for k in z.files}
            if Path(r.seams).name != f"{pre}mosaic_{det}_seams.npz":
                Path(r.seams).unlink(missing_ok=True)
            ctx.output(f"seams_{det}", "npz", seams, f"{pre}mosaic_{det}_seams")
        ctx.metrics.update({f"{det}_tiles": r.info["n_tiles"], f"{det}_width_px": r.info["mosaic_px"][0],
                            f"{det}_height_px": r.info["mosaic_px"][1],
                            f"{det}_residual_max_px": r.info["residual_px"]["max"] or 0.0,
                            f"{det}_stage_only": len(r.info["stage_only_tiles"])})
    ctx.metrics.update({f"count_{k}": v for k, v in counts.items()})


# ---- export of a stitched mosaic (GUI Export dialog, phase 6.6) ----------------------------------------------
EXPORT_FRACTIONS = {"1": 1.0, "1/2": 0.5, "1/4": 0.25, "1/8": 0.125}   # export size as a fraction of the original
MEMORY_BUDGET = 6 * 2**30          # bytes an export may use (peak RAM of the app < 8 GB, CLAUDE.md)
VIEWER_LIMIT_PX = 100e6            # many image viewers / editors fail or crawl above ~100 Mpx (warning only)
PLAIN_TIFF_LIMIT = 3.5 * 2**30     # classic TIFF (PIL) cannot exceed 4 GB: BigTIFF above that


class MosaicExportParams(BaseModel):
    stitch: str | None = None                         # sem.stitch step id, or:
    image: str | None = None                          # a single SEM image (linked file; View tab)
    detector: Literal["BSE", "SE"] = "BSE"
    format: Literal["png", "tiff", "svg", "pdf", "bigtiff"] = "png"   # bigtiff: tiled BigTIFF + pyramid (any size)
    mode: Literal["native", "width", "scale"] = "native"   # native = 1 image pixel per file pixel
    scale: float = Field(1.0, gt=0)                   # mode 'scale': output pixels per image pixel (0.5, 0.25, 2)
    width_mm: float = 120.0
    dpi: int = 600
    scale_bar: bool = True                            # (without legends) scale bar bottom right
    info_strip: bool = True                           # keep the mosaic's info strip / the image's data bar
    legends: list[LegendItem] | None = None           # GUI: scale bar where the viewer shows it (WYSIWYG)
    name: str = ""
    sample_id: str | None = None


def export_size_px(src_w: int, src_h: int, p: MosaicExportParams) -> tuple[int, int, float]:
    """(width, height, scale) of the exported image (without the info strip)."""
    if p.mode == "width":
        w = max(1, int(round(p.width_mm / 25.4 * p.dpi)))
        k = w / src_w
    elif p.mode == "scale":
        k = float(p.scale)
    else:
        k = 1.0
    inv = 1.0 / k
    if k < 1 and abs(inv - round(inv)) < 1e-9:           # 1/2, 1/4, 1/8: PIL reduce (rounds up)
        m = int(round(inv))
        return -(-src_w // m), -(-src_h // m), k
    return max(1, int(round(src_w * k))), max(1, int(round(src_h * k))), k


def export_estimate(src_px: tuple[int, int], out_px: tuple[int, int], fmt: str, src_bytes_per_px: int = 1) -> dict:
    """Pixels, peak memory and file size of a sem.export BEFORE running it, and whether it can run (fix 15 of the
    6.6 user test: a 9767 x 22453 px PNG failed after it was written, at PIL's 179 Mpx decompression-bomb limit in
    the output hash). Memory model (bytes per output pixel): source mosaic read whole (1 B uint8, 2 B uint16 + 4 B
    float for the contrast stretch of 16-bit data), output image 1 B + format: PNG / TIFF ~5 B (output hash decodes
    RGBA in bands, encoder buffers), PDF ~7 B (RGB copy + encoder), SVG ~4 B (PNG buffer + base64 text), BigTIFF
    ~1.4 B (pyramid). Legends are composited per item box (no full-size RGBA copy).
    Returns {pixels, memory_bytes, file_bytes, ok, level ('ok' | 'warn' | 'refuse'), message, alternatives}."""
    sw, sh = src_px
    W, H = out_px
    src = sw * sh * (src_bytes_per_px + (4 if src_bytes_per_px > 1 else 0))
    per = {"png": 5.0, "tiff": 5.0, "pdf": 7.0, "svg": 4.0, "bigtiff": 1.4}[fmt]
    n = W * H
    mem = src + n * (1 + per) + 50e6
    file_b = n * {"png": 0.8, "tiff": 0.8, "pdf": 0.85, "svg": 1.1, "bigtiff": 1.07}[fmt]
    level, msg = "ok", ""
    if fmt != "bigtiff" and mem > MEMORY_BUDGET:
        level = "refuse"
        msg = (f"{W:,} × {H:,} px ({n / 1e6:.0f} Mpx) as {fmt.upper()} needs ≈ {mem / 2**30:.1f} GB of memory (limit "
               f"{MEMORY_BUDGET / 2**30:.0f} GB). Choose a smaller size (1/2, 1/4, 1/8) or the format "
               "'BigTIFF (tiled)'.")
    elif fmt == "tiff" and file_b > PLAIN_TIFF_LIMIT:
        level = "refuse"
        msg = (f"{W:,} × {H:,} px would make a TIFF of ≈ {file_b / 2**30:.1f} GB; a classic TIFF cannot exceed 4 GB. "
               "Choose the format 'BigTIFF (tiled)' or a smaller size.")
    elif fmt == "bigtiff" and mem > MEMORY_BUDGET:
        level = "refuse"
        msg = (f"the mosaic ({sw:,} × {sh:,} px) needs ≈ {mem / 2**30:.1f} GB to export; choose a smaller size "
               "(1/2, 1/4, 1/8)")
    elif fmt != "bigtiff" and n > VIEWER_LIMIT_PX:
        level = "warn"
        msg = (f"{W:,} × {H:,} px ({n / 1e6:.0f} Mpx): many programs cannot open {fmt.upper()} images this large and "
               "writing it takes a while. The format 'BigTIFF (tiled)' opens in Fiji / QuPath; 1/2 or 1/4 is smaller.")
    alts = []
    for lab, k in EXPORT_FRACTIONS.items():
        m = int(round(1 / k))
        w2, h2 = -(-sw // m), -(-sh // m)
        if w2 * h2 < n:
            alts.append({"fraction": lab, "W": w2, "H": h2})
    return {"pixels": n, "memory_bytes": mem, "file_bytes": file_b, "ok": level != "refuse", "level": level,
            "message": msg, "alternatives": alts}


def _burn_items(pil, items, pixel_size_um, info):
    """Legend items composited inside their own box only (legends.compose converts the WHOLE image to RGBA per
    item: ~12 B per pixel, too much for large mosaics). Same pixels as compose."""
    from PIL import Image  # noqa: PLC0415

    from .legends import item_image  # noqa: PLC0415

    for it in items:
        li = item_image(it, pil.width, pixel_size_um, info)
        if li is None:
            continue
        x = int(round(min(max(it.x, 0.0), 1.0) * pil.width))
        y = int(round(min(max(it.y, 0.0), 1.0) * pil.height))
        x = min(x, max(0, pil.width - li.width))
        y = min(y, max(0, pil.height - li.height))
        box = (x, y, min(pil.width, x + li.width), min(pil.height, y + li.height))
        crop = pil.crop(box).convert("RGBA")
        layer = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        layer.paste(li.convert("RGBA"), (0, 0))
        pil.paste(Image.alpha_composite(crop, layer).convert(pil.mode), box[:2])
    return pil


@op("sem.export", MosaicExportParams, uses_backend=False)
def _sem_export(ctx: OpContext) -> None:
    """Export a stitched mosaic (stitch = sem.stitch step) or one SEM image (image = linked file) as PNG / TIFF /
    SVG / PDF or a tiled BigTIFF with pyramid: native pixels, a scale factor (1/2, 1/4, 1/8 ...) or a width in mm at
    a DPI, optional scale bar (from the pixel size; with `legends` exactly where the viewer shows it) and the info
    strip / data bar. The info strip / data bar carries its own scale bar, so no second one is drawn on the image.
    Size and memory are checked first (export_estimate): an export that cannot fit in memory is refused with the
    alternatives, never left to fail half-way. A sidecar JSON records the pixel size of the exported image."""
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
        tif = ctx.project.root / st.outputs[f"mosaic_{det}"].path
        with tifffile.TiffFile(tif) as tf:
            pg = tf.pages[0]
            src_shape, src_bpp = pg.shape[:2], pg.dtype.itemsize
        h_an = int(side["mosaic_px"][1])
        source = f"{p.stitch}:mosaic_{det}"
    elif p.image:
        from .io.bse import read_image  # noqa: PLC0415

        t = stitching.read_tile(p.image)
        ctx.add_input(t.path)
        if t.txt:
            ctx.add_input(t.txt)
        tif = None
        src_shape, src_bpp = (t.file_height, t.file_width), 1
        h_an = t.height
        side = {"pixel_size_um": t.pixel_size_um}
        det = t.detector_class if t.detector_class in ("BSE", "SE") else det
        source = str(Path(t.path))
    else:
        raise ValueError("give stitch (a sem.stitch step) or image (a linked SEM image)")
    W0 = int(src_shape[1])
    W, H, _ = export_size_px(W0, h_an, p)
    strip_h = int(src_shape[0]) - h_an if p.info_strip else 0
    est = export_estimate((W0, int(src_shape[0])), (W, H + int(round(strip_h * W / W0))), p.format, src_bpp)
    if not est["ok"]:
        raise ValueError("export not possible: " + est["message"])
    img = tifffile.imread(tif) if tif is not None else read_image(p.image)
    if img.ndim > 2:
        img = img[..., 0]
    strip = img[h_an:] if img.shape[0] > h_an else None
    im = img[:h_an]
    img = None
    if im.dtype != np.uint8:
        lo, hi = np.percentile(im[:: max(1, im.shape[0] // 2048), :: max(1, im.shape[1] // 2048)], (0.1, 99.9))
        im = np.clip((im.astype(np.float32) - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
    ps = side.get("pixel_size_um")
    pil = Image.fromarray(np.ascontiguousarray(im))
    im = None
    scale = 1.0
    if p.mode == "width" or (p.mode == "scale" and abs(p.scale - 1.0) > 1e-9):
        W, H, scale = export_size_px(pil.width, pil.height, p)
        inv = 1.0 / scale
        if scale < 1 and abs(inv - round(inv)) < 1e-9:      # 1/2, 1/4, 1/8: box average (exact, low memory)
            pil = pil.reduce(int(round(inv)))
        else:
            pil = pil.resize((W, H), Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.NEAREST)
    out_ps = ps / scale if ps else None
    with_strip = bool(p.info_strip and strip is not None and strip.size)
    # the info strip / data bar has its own scale bar: never draw a second one on the image (user test, fix 16)
    items = [it for it in (p.legends or []) if not (with_strip and it.kind == "scale_bar")]
    dropped = len(p.legends or []) - len(items)
    if p.legends is not None:
        pil = _burn_items(pil, items, out_ps, {})
    elif p.scale_bar and out_ps and not with_strip:
        L, bar = nice_bar(out_ps, 0.2 * pil.width)
        cap = max(10, pil.width // 70)
        font = _font(cap)
        d = ImageDraw.Draw(pil)
        x1, y1 = pil.width - cap * 2, pil.height - cap * 2
        x0 = x1 - bar
        d.rectangle([x0 - cap, y1 - cap * 3.2, x1 + cap, y1 + cap], fill=255)
        d.rectangle([x0, y1 - cap * 0.5, x1, y1], fill=0)
        d.text(((x0 + x1) / 2, y1 - cap * 0.9), bar_label(L), fill=0, font=font, anchor="ms")
    if with_strip:
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
    ext = {"png": ".png", "tiff": ".tif", "svg": ".svg", "pdf": ".pdf", "bigtiff": ".tif"}[p.format]
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    out = ctx.out_dir / f"{stem}{ext}"
    dpi = (p.dpi, p.dpi) if p.mode == "width" else ((25400 / out_ps,) * 2 if out_ps else (300, 300))
    kind = p.format
    size = (pil.width, pil.height)
    if p.format == "png":
        pil.save(out, dpi=dpi)
    elif p.format == "tiff":
        pil.save(out, compression="tiff_deflate", dpi=dpi)
    elif p.format == "bigtiff":
        arr = np.asarray(pil)
        pil = None
        stitching.write_tiff(out, arr, out_ps, bigtiff=True,
                             description={"ti64app": "sem.export", "source": source, "pixel_size_um": out_ps})
        kind = "mosaic"                                   # tiled BigTIFF: hashed by its bytes (no full decode)
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
    ctx.output("export", kind, out, stem)
    ctx.output("sidecar", "json", {"source": source, "detector": det, "format": p.format,
                                   "image_px": list(size), "pixel_size_um": out_ps, "scale": scale,
                                   "info_strip": with_strip,
                                   "scale_bar": (True if with_strip else p.scale_bar if p.legends is None else
                                                 any(it.kind == "scale_bar" for it in items)),
                                   "scale_bar_source": ("info strip" if with_strip else "image"),
                                   "scale_bar_left_out": dropped > 0,
                                   "estimate": {k: est[k] for k in ("pixels", "memory_bytes", "file_bytes", "level")},
                                   "legends": [it.model_dump() for it in items]}, stem)
    ctx.metrics.update(export_px=float(size[0] * size[1]))
