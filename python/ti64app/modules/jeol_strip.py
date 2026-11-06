"""JEOL JSM-IT500HR data bar ("info strip") under SEM images: field values for a mosaic, a newly drawn strip in the
same look, and measurement of a strip's layout (scale bar, text positions) for tests.

The original strip (black, white sans-serif text, two rows) at 2560 px image width is 128 px high and scales with the
resolution (64 px at 1280, 256 px at 5120: every position x 2). Measured on data/test/stitch (2026-11-06), in px of a
128-px strip of a 2560-px image:
  row 1 caps top 18 / bottom 51:  detector (centred at x = 87), kV (x = 195), WD (389), Std.-PC (743),
                                  camera icon (1278-1333, rows 5-51) + magnification (x = 1342),
                                  scale bar (rows 18-45, right end at width - 270) + length (centred at width - 167)
  row 2 caps top 82 / bottom 115: image number (x = 27), date (x = 202)
The generated strip keeps these positions (left fields from the left edge, camera at the centre, scale bar from the
right edge), so for a single-column mosaic it lines up with the original. Font: DejaVu Sans bundled with matplotlib
(identical on every machine, so replays reproduce the pixels; the JEOL font is similar but not identical).
"""
from __future__ import annotations

import math
from datetime import date as _date
from pathlib import Path

import numpy as np

REF_H = 128.0                    # layout below is in px of a 128-px strip
CAP_TOP, CAP_H = 18.0, 34.0
ROW2_TOP = 82.0
X_DET_CENTRE, X_KV, X_WD, X_PC = 87.0, 195.0, 389.0, 743.0
CAM_FROM_CENTRE, CAM_W, CAM_TOP, CAM_BOTTOM = -2.0, 55.0, 5.0, 52.0
X_MAG_AFTER_CAM = 9.0            # gap between the icon and the "x5,000" text
BAR_RIGHT_FROM_EDGE, BAR_TOP, BAR_BOTTOM = 270.0, 18.0, 46.0
LABEL_CENTRE_FROM_EDGE = 167.0
X_NUM, X_DATE = 27.0, 202.0
MIN_GAP = 30.0
SQUEEZE = 0.86                   # DejaVu Sans is ~16 % wider than the JEOL font: text is condensed horizontally


def _font_path() -> str:
    import matplotlib  # noqa: PLC0415

    return str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


def _font(cap_px: float):
    """DejaVu Sans at the size whose capital H is cap_px high (ink)."""
    from PIL import ImageFont  # noqa: PLC0415

    path = _font_path()
    size = max(6, int(round(cap_px / 0.729)))
    for _ in range(6):
        f = ImageFont.truetype(path, size)
        b = f.getbbox("H")
        h = b[3] - b[1]
        if abs(h - cap_px) < 0.6:
            break
        size = max(6, int(round(size * cap_px / max(h, 1))))
    return ImageFont.truetype(path, size)


# ---- field values -------------------------------------------------------------------------------
def _rng(vals: list[float], fmt: str) -> str:
    v = sorted({round(x, 6) for x in vals if x is not None})
    if not v:
        return "?"
    return fmt.format(v[0]) if len(v) == 1 else f"{fmt.format(v[0])}–{fmt.format(v[-1])}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _jeol_date(s: str) -> str:
    """'2026-10-29' -> 'Oct. 29 2026' (English month names as on the JEOL strip; never strftime('%b'), which follows
    the process locale - e.g. 'říj.' once something sets a Czech locale - and would break replays)."""
    try:
        d = _date.fromisoformat(s)
    except ValueError:
        return s
    return f"{_MONTHS[d.month - 1]}. {d.day} {d.year}"


def image_numbers_text(nums: list[int]) -> str:
    nums = sorted({n for n in nums if n is not None})
    if not nums:
        return ""
    if len(nums) == 1:
        return str(nums[0])
    n = len(nums)
    if nums[-1] - nums[0] + 1 == n:
        return f"{nums[0]}–{nums[-1]}, {n} tiles"
    if n <= 4:
        return ", ".join(map(str, nums)) + f" ({n} tiles)"
    return f"{nums[0]}–{nums[-1]}, {n} tiles (not consecutive)"


def mosaic_fields(tiles, pixel_size_um: float | None) -> dict:
    """Strip texts for a set of tiles (TileInfo). Values that differ between tiles are shown as a range."""
    dets = sorted({t.detector for t in tiles if t.detector})
    dates = sorted({t.date for t in tiles if t.date})
    mags = sorted({t.magnification for t in tiles if t.magnification})
    probes = sorted({t.probe for t in tiles if t.probe})
    if len(dates) > 1:
        a, b = _jeol_date(dates[0]), _jeol_date(dates[-1])
        date_txt = f"{a} – {b}"
    else:
        date_txt = _jeol_date(dates[0]) if dates else ""
    return {
        "detector": "/".join(dets),
        "kv": _rng([t.kv for t in tiles], "{:.1f}") + " kV",
        "wd": "WD " + _rng([t.wd_mm for t in tiles], "{:.1f}") + " mm",
        "probe": "Std.-PC " + (probes[0] if len(probes) == 1 else "–".join([probes[0], probes[-1]]) if probes else "?"),
        "mag": "x" + ("–".join(f"{int(round(m)):,}" for m in ([mags[0], mags[-1]] if len(mags) > 1 else mags))
                      if mags else "?"),
        "numbers": image_numbers_text([t.image_number for t in tiles]),
        "date": date_txt,
        "pixel_size_um": pixel_size_um,
    }


def tile_fields(t) -> dict:
    """Fields of one tile (to redraw its own strip, e.g. to compare with the original)."""
    return mosaic_fields([t], t.pixel_size_um)


def max_bar_px(tiles) -> int:
    """Longest scale bar JEOL drew on these tiles (SM_MICRON_BAR, in image px); ~20 % of the width otherwise."""
    bars = [t.micron_bar_px for t in tiles if t.micron_bar_px]
    return int(max(bars)) if bars else int(round(0.2 * tiles[0].width))


def nice_bar(pixel_size_um: float, max_px: float) -> tuple[float, int]:
    """Largest 1 / 2 / 5 x 10^n um length whose bar is <= max_px. Returns (length um, length px)."""
    best = None
    for e in range(-3, 6):
        for m in (1, 2, 5):
            L = m * 10.0 ** e
            px = L / pixel_size_um
            if px <= max_px + 1e-6:
                best = (L, int(round(px)))
    if best is None:
        L = 10.0 ** -3
        best = (L, max(1, int(round(L / pixel_size_um))))
    return best


def bar_label(L_um: float) -> str:
    if L_um >= 1000:
        return f"{L_um / 1000:g} mm"
    if L_um < 1:
        return f"{L_um * 1000:g} nm"
    return f"{L_um:g} µm"


# ---- drawing -------------------------------------------------------------------------------------
def _camera(draw, x0: float, k: float) -> float:
    """Camera icon (outline body, top bump, lens), returns its right edge."""
    lw = max(1, int(round(4 * k)))
    x1 = x0 + CAM_W * k
    top, bot = CAM_TOP * k, (CAM_BOTTOM - 1) * k
    body_top = top + 9 * k
    draw.rounded_rectangle([x0, body_top, x1, bot], radius=max(1, int(4 * k)), outline=255, width=lw)
    bx0, bx1 = x0 + 14 * k, x0 + 38 * k
    draw.rectangle([bx0, top, bx1, body_top + lw], fill=255)
    cx, cy, r = x0 + 27.5 * k, (body_top + bot) / 2 + 1 * k, 12.5 * k
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=lw)
    return x1


def _text(img, font, x: float, cap_top: float, s: str, anchor: str = "left") -> tuple[float, float]:
    """Draw s (condensed by SQUEEZE) with its ink starting at x (or centred on x) and its capitals' top at cap_top.
    Returns the ink x-range."""
    from PIL import Image, ImageChops, ImageDraw  # noqa: PLC0415

    if not s:
        return x, x
    hb = font.getbbox("H")
    b = font.getbbox(s)
    tmp = Image.new("L", (b[2] - b[0] + 4, img.height), 0)
    ImageDraw.Draw(tmp).text((2 - b[0], cap_top - hb[1]), s, font=font, fill=255)
    w = max(1, int(round((b[2] - b[0]) * SQUEEZE)))
    tmp = tmp.crop((2, 0, 2 + b[2] - b[0], img.height)).resize((w, img.height), Image.Resampling.LANCZOS)
    left = int(round(x - w / 2 if anchor == "centre" else x))
    region = (left, 0, min(img.width, left + w), img.height)
    if region[2] > region[0] and left >= 0:
        cur = img.crop(region)
        img.paste(ImageChops.lighter(cur, tmp.crop((0, 0, region[2] - region[0], img.height))), region[:2])
    return float(left), float(left + w)


def render_strip(fields: dict, width: int, height: int, pixel_size_um: float | None, ref_width: int | None = None,
                 max_bar_px: int | None = None) -> tuple[np.ndarray, dict]:
    """A new JEOL-style data bar `height` px high and `width` px wide (uint8). Returns (image, info) with the drawn
    scale bar (um, px, x-range) and the start of every field (px)."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    k = height / REF_H
    img = Image.new("L", (int(width), int(height)), 0)
    d = ImageDraw.Draw(img)
    font = _font(CAP_H * k)
    pos: dict[str, list[float]] = {}
    gap = MIN_GAP * k
    # row 1, left block
    pos["detector"] = list(_text(img, font, X_DET_CENTRE * k, CAP_TOP * k, fields.get("detector", ""), "centre"))
    end = pos["detector"][1]
    for key, col in (("kv", X_KV), ("wd", X_WD), ("probe", X_PC)):
        pos[key] = list(_text(img, font, max(col * k, end + gap), CAP_TOP * k, fields.get(key, "")))
        end = pos[key][1]
    # camera + magnification at the centre
    cam_x = max(width / 2 + CAM_FROM_CENTRE * k, end + gap)
    cam_r = _camera(d, cam_x, k)
    pos["camera"] = [cam_x, cam_r]
    pos["mag"] = list(_text(img, font, cam_r + X_MAG_AFTER_CAM * k, CAP_TOP * k, fields.get("mag", "")))
    # scale bar from the right edge
    info: dict = {"positions": pos, "font": "DejaVu Sans", "height_px": int(height), "scale": k}
    if pixel_size_um:
        L, px = nice_bar(pixel_size_um, max_bar_px or 0.2 * (ref_width or width))
        right = width - BAR_RIGHT_FROM_EDGE * k
        left = right - px
        if left < pos["mag"][1] + gap:                     # narrow mosaic: shrink to the next shorter length
            L, px = nice_bar(pixel_size_um, max(right - pos["mag"][1] - gap, 1))
            left = right - px
        d.rectangle([left, BAR_TOP * k, right - 1, BAR_BOTTOM * k - 1], fill=255)
        label = bar_label(L)
        pos["bar"] = [left, right]
        pos["bar_label"] = list(_text(img, font, width - LABEL_CENTRE_FROM_EDGE * k, CAP_TOP * k, label, "centre"))
        info.update(bar_um=L, bar_px=int(px), bar_label=label)
    # row 2
    pos["numbers"] = list(_text(img, font, X_NUM * k, ROW2_TOP * k, fields.get("numbers", "")))
    pos["date"] = list(_text(img, font, max(X_DATE * k, pos["numbers"][1] + gap), ROW2_TOP * k, fields.get("date", "")))
    return np.asarray(img), info


# ---- measuring a strip ---------------------------------------------------------------------------
def find_scale_bar(strip: np.ndarray) -> dict | None:
    """The scale bar = the widest bright connected component that is a filled rectangle (fill > 90 %)."""
    from scipy import ndimage as ndi  # noqa: PLC0415

    m = strip > 128
    lab, n = ndi.label(m)
    best = None
    for k, sl in enumerate(ndi.find_objects(lab), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        fill = (lab[sl] == k).mean()
        if w > 4 * h and fill > 0.9 and (best is None or w > best["length_px"]):
            best = {"x0": sl[1].start, "x1": sl[1].stop, "y0": sl[0].start, "y1": sl[0].stop, "length_px": w}
    return best


def text_rows(strip: np.ndarray) -> list[tuple[int, int]]:
    """Vertical extents (start, stop) of the bright rows of the two text rows (the camera icon and scale bar are
    excluded by looking at the left third only)."""
    m = (strip[:, : strip.shape[1] // 3] > 128).any(axis=1)
    rows, start = [], None
    for y, v in enumerate(m):
        if v and start is None:
            start = y
        elif not v and start is not None:
            rows.append((start, y))
            start = None
    if start is not None:
        rows.append((start, len(m)))
    return rows


def group_starts(strip: np.ndarray, row: int, min_gap: float | None = None) -> list[int]:
    """x where the ink groups of text row `row` (0 / 1) start (groups separated by > min_gap px of black)."""
    h = strip.shape[0]
    band = strip[: h // 2] if row == 0 else strip[h // 2:]
    cols = np.nonzero((band > 128).any(axis=0))[0]
    if cols.size == 0:
        return []
    g = min_gap if min_gap is not None else 0.2 * h
    starts = [int(cols[0])] + [int(c) for a, c in zip(cols[:-1], cols[1:]) if c - a > g]
    return starts


def strip_height_scale(width: int, ref_width: int = 2560, ref_height: int = 128) -> int:
    return int(round(ref_height * width / ref_width))


