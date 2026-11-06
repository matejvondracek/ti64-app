"""Legends drawn ON an exported map / image exactly where the viewer shows them (what you see is what you get):
scale bar, colour bar (value maps) or colour swatches (phase / ID maps), IPF colour key. Python-only, shared by
export.map (both backends) and sem.export.

A legend item is placed in fractions of the exported image: x, y = its top-left corner, w = its width (0..1 of the
image width), the same numbers the viewer stores when the user drags / resizes the box over the map. Raster exports
(PNG / TIFF) get the items burnt in; SVG gets them as embedded images (the map stays vector); PDF gets an overlay
page merged on top (pypdf).
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from PIL import Image

LegendKind = Literal["scale_bar", "colorbar", "ipf_key"]
DEFAULTS = {"scale_bar": (0.03, 0.88, 0.22), "colorbar": (0.68, 0.86, 0.28), "ipf_key": (0.80, 0.08, 0.17)}


class LegendItem(BaseModel):
    kind: LegendKind
    x: float = Field(0.03, ge=-0.5, le=1.5, description="left edge, fraction of the image width")
    y: float = Field(0.88, ge=-0.5, le=1.5, description="top edge, fraction of the image height")
    w: float = Field(0.2, gt=0, le=1.0, description="width, fraction of the image width")


def default_item(kind: str) -> LegendItem:
    x, y, w = DEFAULTS[kind]
    return LegendItem(kind=kind, x=x, y=y, w=w)


def _font(px: float):
    from .modules.jeol_strip import _font as jfont  # noqa: PLC0415

    return jfont(max(5.0, px))


def scale_bar_image(width_px: int, pixel_size_um: float) -> "Image.Image":
    """White rounded box with a black bar of a nice length (<= 80 % of the box) and its label."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    from .modules.jeol_strip import bar_label, nice_bar  # noqa: PLC0415

    W = max(24, int(round(width_px)))
    L, bar = nice_bar(pixel_size_um, 0.8 * W)
    cap = max(6.0, W / 14)
    H = int(round(cap * 3.4))
    im = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=int(cap * 0.5), fill=(255, 255, 255, 225))
    x0 = (W - bar) / 2
    d.rectangle([x0, H - cap * 1.25, x0 + bar, H - cap * 0.6], fill=(0, 0, 0, 255))
    d.text((W / 2, H - cap * 1.55), bar_label(L), fill=(0, 0, 0, 255), font=_font(cap), anchor="ms")
    return im


def colorbar_image(width_px: int, info: dict) -> "Image.Image | None":
    """Horizontal colour bar (info: colorbar = (k, 3|4) uint8 or hex list, clim, label) or colour swatches
    (info: legend = [(label, rgb 0..1 or hex)])."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    W = max(40, int(round(width_px)))
    cap = max(6.0, W / 22)
    stops = info.get("colorbar")
    legend = info.get("legend") or []
    if stops is not None and len(stops) and info.get("clim"):
        cols = np.array([_rgb8(c) for c in stops], np.uint8)
        H = int(round(cap * 5.2))
        im = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=int(cap * 0.5), fill=(255, 255, 255, 225))
        bx0, bx1 = int(cap), int(W - cap)
        by0, by1 = int(cap * 0.8), int(cap * 2.2)
        t = np.linspace(0, len(cols) - 1, max(1, bx1 - bx0)).round().astype(int)
        strip = np.repeat(cols[t][None, :, :3], by1 - by0, axis=0)
        im.paste(Image.fromarray(np.ascontiguousarray(strip), "RGB"), (bx0, by0))
        d.rectangle([bx0, by0, bx1 - 1, by1 - 1], outline=(0, 0, 0, 255), width=1)
        lo, hi = info["clim"]
        f = _font(cap)
        d.text((bx0, by1 + cap * 1.5), f"{lo:.3g}", fill=(0, 0, 0, 255), font=f, anchor="ls")
        d.text((bx1, by1 + cap * 1.5), f"{hi:.3g}", fill=(0, 0, 0, 255), font=f, anchor="rs")
        lab = (info.get("colorbar_label") or "").strip()
        if lab:
            d.text((W / 2, H - cap * 0.5), lab, fill=(0, 0, 0, 255), font=_font(cap * 0.9), anchor="ms")
        return im
    if legend:
        n = len(legend)
        row = cap * 1.9
        H = int(round(cap + n * row))
        im = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=int(cap * 0.5), fill=(255, 255, 255, 225))
        f = _font(cap)
        for k, (lab, c) in enumerate(legend):
            y = cap * 0.6 + k * row
            d.rectangle([cap, y, cap * 2.4, y + cap * 1.2], fill=tuple(_rgb8(c)) + (255,), outline=(90, 90, 90, 255))
            d.text((cap * 3.0, y + cap * 1.1), str(lab), fill=(0, 0, 0, 255), font=f, anchor="ls")
        return im
    return None


def key_image(width_px: int, pngs: list[str | Path]) -> "Image.Image | None":
    """IPF colour key(s) (backend-rendered PNGs, side by side) scaled to width_px on a white box."""
    from PIL import Image  # noqa: PLC0415

    ims = [Image.open(p).convert("RGBA") for p in pngs if p and Path(p).is_file()]
    if not ims:
        return None
    gap = 8
    W0 = sum(i.width for i in ims) + gap * (len(ims) - 1)
    H0 = max(i.height for i in ims)
    both = Image.new("RGBA", (W0, H0), (255, 255, 255, 255))
    x = 0
    for i in ims:
        both.paste(i, (x, (H0 - i.height) // 2))
        x += i.width + gap
    W = max(24, int(round(width_px)))
    return both.resize((W, max(1, int(round(H0 * W / W0)))), Image.Resampling.LANCZOS)


def _rgb8(c) -> list[int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    c = list(c)[:3]
    return [int(round(255 * float(v))) if max(c) <= 1.0 else int(v) for v in c]


def item_image(it: LegendItem, image_w: int, pixel_size_um: float | None, info: dict) -> "Image.Image | None":
    wpx = it.w * image_w
    if it.kind == "scale_bar":
        return scale_bar_image(wpx, pixel_size_um) if pixel_size_um else None
    if it.kind == "colorbar":
        return colorbar_image(wpx, info)
    return key_image(wpx, info.get("ipf_key_pngs") or [])


def compose(img, items: list[LegendItem], pixel_size_um: float | None, info: dict):
    """Burn the legend items into a PIL image (RGB / RGBA / L); returns the new image (same mode)."""
    from PIL import Image  # noqa: PLC0415

    if not items:
        return img
    mode = img.mode
    base = img.convert("RGBA")
    for it in items:
        li = item_image(it, base.width, pixel_size_um, info)
        if li is None:
            continue
        x = int(round(min(max(it.x, 0.0), 1.0) * base.width))
        y = int(round(min(max(it.y, 0.0), 1.0) * base.height))
        x = min(x, max(0, base.width - li.width))
        y = min(y, max(0, base.height - li.height))
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        layer.paste(li, (x, y))
        base = Image.alpha_composite(base, layer)
    if mode in ("RGB", "L"):
        return base.convert(mode)
    return base


def compose_array(arr: np.ndarray, items: list[LegendItem], pixel_size_um: float | None, info: dict) -> np.ndarray:
    from PIL import Image  # noqa: PLC0415

    if not items:
        return arr
    im = Image.fromarray(np.ascontiguousarray(arr))
    return np.asarray(compose(im, items, pixel_size_um, info))


def compose_svg(path: Path, items: list[LegendItem], pixel_size_um: float | None, info: dict) -> None:
    """Insert the legend items into an SVG file as embedded PNG images (the drawing stays vector)."""
    s = Path(path).read_text(encoding="utf-8")
    m = re.search(r'viewBox="([-\d.eE ]+)"', s)
    if not m:
        return
    vx, vy, vw, vh = (float(v) for v in m.group(1).split())
    render_w = 2000                              # legend images rendered at 2000 px per image width
    k = vw / render_w                            # SVG units per legend pixel
    parts = []
    for it in items:
        # pixel_size_um = um per SVG unit of the map -> um per legend pixel
        li = item_image(it, render_w, pixel_size_um * k if pixel_size_um else None, info)
        if li is None:
            continue
        buf = io.BytesIO()
        li.save(buf, "PNG")
        w, h = li.width * k, li.height * k
        x = vx + min(max(it.x, 0), 1) * vw
        y = vy + min(max(it.y, 0), 1) * vh
        x, y = min(x, vx + vw - w), min(y, vy + vh - h)
        parts.append(f'<image x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
                     f'xlink:href="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"/>')
    if not parts:
        return
    if "xmlns:xlink" not in s[:2000]:
        s = s.replace("<svg ", '<svg xmlns:xlink="http://www.w3.org/1999/xlink" ', 1)
    i = s.rfind("</svg>")
    Path(path).write_text(s[:i] + "\n".join(parts) + "\n" + s[i:], encoding="utf-8")


def compose_pdf(path: Path, items: list[LegendItem], pixel_size_um_per_pt: float | None, info: dict) -> None:
    """Merge an overlay page with the legend items onto page 1 of a PDF (pypdf). pixel_size_um_per_pt = um per
    PDF point of the map."""
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    reader = PdfReader(str(path))
    page = reader.pages[0]
    W, H = float(page.mediabox.width), float(page.mediabox.height)
    render_w = 2000
    k = W / render_w                                 # points per legend pixel
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    fig = plt.figure(figsize=(W / 72, H / 72))
    n = 0
    for it in items:
        li = item_image(it, render_w, pixel_size_um_per_pt * k if pixel_size_um_per_pt else None, info)
        if li is None:
            continue
        w, h = li.width * k, li.height * k
        x = min(max(it.x, 0), 1) * W
        y = min(max(it.y, 0), 1) * H
        x, y = min(x, W - w), min(y, H - h)
        ax = fig.add_axes([x / W, 1 - (y + h) / H, w / W, h / H])
        ax.imshow(np.asarray(li), interpolation="lanczos")
        ax.set_axis_off()
        n += 1
    if n == 0:
        plt.close(fig)
        return
    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", transparent=True, metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    over = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    page.merge_page(over)
    w = PdfWriter()
    w.add_page(page)
    for p in reader.pages[1:]:
        w.add_page(p)
    with open(path, "wb") as fh:
        w.write(fh)


def describe(items: list[LegendItem]) -> list[dict]:
    return [it.model_dump() for it in items]
