"""Stitching of overlapping SEM images (JEOL JSM-IT500HR BSE / SE tiles) into one mosaic. Python-only: works with
either analysis backend and never calls one.

    tiles = scan(folder=...)                       # image + .txt pairs, detector, data-bar height, stage, pixel size
    counts = detector_counts(tiles)                # {"BSE": 8, "SE": 8}
    res = stitch(StitchParams(folder=..., detector="BSE"), out_dir)   # -> {"BSE": MosaicResult}

Method (docs/decisions.md D17)
1. Detector from $SM_DETECTOR (BED-* = BSE, SED/LED/UED/... = SE), file name as fallback; detectors never mixed.
2. Data bar (info strip) cropped: height = file height - $CM_IMAGE_SIZE height (= $SM_PNU_HEIGHT); without a .txt the
   JEOL image area is 4:3 (height = width * 3/4), confirmed by detecting the black strip with white text.
3. Pixel size = $CM_FIELD_OF_VIEW width / image width. Tiles with another pixel size, magnification, scan rotation
   or stage rotation / tilt are refused unless `allow_rescale`.
4. Initial layout from $CM_STAGE_POSITION (1 um resolution = only a starting guess). The stage -> image axis mapping
   (which image axis, which sign) is chosen from the data: every signed axis permutation is tried and the one whose
   predicted overlaps correlate best wins. Without stage data all pairs are tried (downsampled phase correlation).
5. Pairwise registration: translation only. Phase correlation of the predicted overlap (Hann window, the strongest
   peaks with all wrap-around interpretations, plus the best offset of a masked NCC surface within the search window;
   each candidate scored by normalized cross-correlation (NCC) of the real overlap, at 1/f resolution), then subpixel refinement with skimage.registration.phase_cross_correlation at full resolution. Weak pairs
   (NCC < min_score) or pairs far from the stage prediction are rejected.
6. Global placement: weighted least squares over all good pairs, weakly anchored to the stage positions, with
   iterative removal of the worst pair while its residual exceeds max_residual_px - as in Fiji Grid/Collection
   stitching (Preibisch et al. 2009, doi:10.1093/bioinformatics/btp184) and NIST MIST (Chalfoun et al. 2017,
   doi:10.1038/s41598-017-04567-y). Tiles without a good pair are placed by the stage only and flagged.
7. Per-tile brightness / contrast normalisation (gain + offset from the overlap statistics, global least squares),
   then linear feathering in the overlaps (blend="feather") or no blending (blend="none": each pixel from the one
   tile whose edge is farthest, raw values, no normalisation) for measurements on raw pixels.
   Tiles are placed at integer pixel positions (no resampling); the subpixel positions are reported.
8. Output: TIFF (BigTIFF + pyramid when large, tifffile), PNG preview, JSON sidecar (inputs, detector, positions in
   px and um, pixel size, stage -> mosaic transform, match scores, residuals, data-bar height). Rendering is done in
   bands into a disk-backed array so peak RAM stays far below 8 GB for large mosaics.
"""
from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel, Field

from ..io import bse as bse_io

IMAGE_SUFFIXES = (".bmp", ".tif", ".tiff", ".png", ".jpg", ".jpeg")
DetectorClass = Literal["BSE", "SE", "other"]

# Stage -> image mapping measured on the JSM-IT500HR (decisions D17): +stage Y (mm) moves the field of view DOWN in
# the image (+row), +stage X moves it to the LEFT (-col). Used when the data cannot decide (stage moved along one
# axis only) and for the thumbnail preview before registration. Matrix rows = (col, row), columns = (X, Y), in px/um
# after multiplying by 1 / pixel size.
DEFAULT_STAGE_MAP = ((-1, 0), (0, 1))

PROGRESS: dict[str, Callable[[str, float], None] | None] = {"hook": None}


def _progress(msg: str, frac: float) -> None:
    hook = PROGRESS["hook"]
    if hook:
        hook(msg, float(min(max(frac, 0.0), 1.0)))


# ---- tiles ----------------------------------------------------------------------------------------
class TileInfo(BaseModel):
    """One SEM image and what its .txt sidecar (if any) says about it."""
    path: str
    txt: str | None = None
    name: str                              # file stem
    detector: str = ""                     # $SM_DETECTOR or the token from the file name (BED-C, SED, ...)
    detector_class: DetectorClass = "other"
    detector_source: str = ""              # "txt" | "file name" | ""
    image_number: int | None = None        # $SM_IMAGE_NUMBER, else the leading number of the file name
    file_width: int
    file_height: int
    height: int                            # strip-free image height
    strip_px: int = 0                      # data-bar height cropped at the bottom
    strip_source: str = ""                 # "txt" | "detected" | "4:3 rule" | "manual" | "none"
    pixel_size_um: float | None = None
    magnification: float | None = None
    scan_rotation_deg: float | None = None
    stage_x_mm: float | None = None
    stage_y_mm: float | None = None
    stage_z_mm: float | None = None
    stage_rotation_deg: float | None = None
    stage_tilt_deg: float | None = None
    beam_shift: tuple[float, ...] | None = None    # $MP_BEAM_SHIFT (instrument units)
    kv: float | None = None
    wd_mm: float | None = None
    probe: str = ""                        # $MP_PROBE_NUMBER (Std.-PC)
    date: str = ""
    time: str = ""
    micron_marker: str = ""
    micron_bar_px: int | None = None
    warnings: list[str] = []

    @property
    def width(self) -> int:
        return self.file_width

    @property
    def has_stage(self) -> bool:
        return self.stage_x_mm is not None and self.stage_y_mm is not None


_DET_RE = re.compile(r"(?:^|[_\-\s])(BED-[A-Z]|BED|BEI|BSE|COMPO|SED|SEI|LED|UED|USD|SE)(?=$|[_\-\s.\d])", re.I)


def classify_detector(name: str) -> DetectorClass:
    """BED-C / BED-T / BED / BSE / COMPO -> BSE; SED / SEI / LED / UED / USD / SE -> SE."""
    n = name.strip().upper()
    if n.startswith(("BED", "BSE", "BEI", "COMPO")):
        return "BSE"
    if n.startswith(("SED", "SEI", "LED", "UED", "USD")) or n == "SE":
        return "SE"
    return "other"


def detector_from_filename(stem: str) -> str:
    hits = _DET_RE.findall(stem)
    return hits[-1].upper() if hits else ""


def detect_strip(img: np.ndarray, expected: int | None = None) -> int | None:
    """Height of a JEOL data bar (black background, white text) at the bottom of `img`, or None.
    The bar is a block of bottom rows that are nearly all black except for bright text/scale-bar pixels, directly
    under the (grey, textured) image. If `expected` is given (4:3 rule), only that height is checked and only the strip
    itself (black + white, text in both rows) is tested, so dark overview images (black chamber above) also pass."""
    h, w = img.shape[:2]
    g = img if img.ndim == 2 else img[..., :3].mean(axis=2)
    g = g.astype(np.float32)
    if g.max() > 255:
        g = g * (255.0 / g.max())
    dark = (g < 40).mean(axis=1)            # fraction of near-black pixels per row
    bright = (g > 200).mean(axis=1)

    def is_strip(k: int, check_above: bool = True) -> bool:
        if k < 8 or k > h // 3:
            return False
        rows = slice(h - k, h)
        if (dark[rows] + bright[rows]).mean() < 0.97:                     # black + white text only
            return False
        top, bottom = bright[h - k: h - k // 2], bright[h - k // 2: h]   # two text rows
        if top.max() < 0.01 or bottom.max() < 0.005:
            return False
        if not check_above:                  # expected height known (4:3 rule / .txt): the strip itself suffices
            return True
        above = slice(max(0, h - k - 16), h - k)                      # image above: not black + white only
        return (dark[above] + bright[above]).mean() < 0.9

    if expected:
        return int(expected) if is_strip(int(expected), check_above=False) else None
    for k in sorted({h - int(round(w * 3 / 4)), 64, 128, 256}):
        if k and is_strip(int(k)):
            return int(k)
    # free search: first row from the bottom where the image starts
    blackwhite = (dark + bright) > 0.97
    k = 0
    while k < h // 3 and blackwhite[h - 1 - k]:
        k += 1
    return k if is_strip(k) else None


def _num(raw: dict, key: str) -> float | None:
    v = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw.get(key, ""))
    return float(v[0]) if v else None


def read_tile(path: str | Path, txt: str | Path | None = None, strip_px: int | None = None) -> TileInfo:
    """Metadata of one image (pixels are not kept). `strip_px` overrides the data-bar height."""
    from PIL import Image  # noqa: PLC0415

    path = Path(path)
    if txt is None:
        txt = bse_io.find_sidecar(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile  # noqa: PLC0415

        with tifffile.TiffFile(path) as tf:
            fh, fw = tf.pages[0].shape[:2]
    else:
        with Image.open(path) as im:
            fw, fh = im.size
    stem = path.stem
    t = TileInfo(path=str(path), txt=str(txt) if txt else None, name=stem, file_width=fw, file_height=fh, height=fh)
    lead = re.match(r"(\d+)", stem)
    if lead:
        t.image_number = int(lead.group(1))
    meta = None
    if txt:
        meta = bse_io.read_metadata(txt, image_shape=(fh, fw))
        raw = meta.raw
        det = raw.get("SM_DETECTOR", "") or raw.get("SM_DETECTOR1", "")
        if det:
            t.detector, t.detector_source = det.strip(), "txt"
        if (n := _num(raw, "SM_IMAGE_NUMBER")) is not None:
            t.image_number = int(n)
        t.scan_rotation_deg = _num(raw, "SM_SCAN_ROTATION")
        if raw.get("MP_BEAM_SHIFT"):
            t.beam_shift = tuple(float(v) for v in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw["MP_BEAM_SHIFT"]))
        t.probe = raw.get("MP_PROBE_NUMBER", "")
        t.time = raw.get("CM_TIME", "")
        t.micron_marker = raw.get("SM_MICRON_MARKER", "")
        if (b := _num(raw, "SM_MICRON_BAR")) is not None:
            t.micron_bar_px = int(b)
        for a, b in (("pixel_size_um", "pixel_size_um"), ("magnification", "magnification"),
                     ("stage_x_mm", "stage_x_mm"), ("stage_y_mm", "stage_y_mm"), ("stage_z_mm", "stage_z_mm"),
                     ("stage_rotation_deg", "stage_rotation_deg"), ("stage_tilt_deg", "stage_tilt_deg"),
                     ("kv", "accelerating_voltage_kV"), ("wd_mm", "working_distance_mm"), ("date", "date")):
            setattr(t, a, getattr(meta, b))
    if not t.detector:
        d = detector_from_filename(stem)
        if d:
            t.detector, t.detector_source = d, "file name"
    t.detector_class = classify_detector(t.detector) if t.detector else "other"
    # data bar
    if strip_px is not None:
        t.strip_px, t.strip_source = int(strip_px), "manual"
    elif meta is not None and meta.image_size_px and meta.image_size_px[0] == fw and meta.image_size_px[1] <= fh:
        t.strip_px, t.strip_source = fh - meta.image_size_px[1], "txt"
        if meta.data_bar_px is not None and meta.data_bar_px != t.strip_px:
            t.warnings.append(f"SM_PNU_HEIGHT {meta.data_bar_px} != file height - CM_IMAGE_SIZE height {t.strip_px}")
    elif meta is not None and meta.image_size_px and meta.image_size_px[0] and meta.image_size_px[0] != fw:
        # saved at another size than the image area: scale the image-area height
        k = fh - int(round(meta.image_size_px[1] * fw / meta.image_size_px[0]))
        t.strip_px, t.strip_source = max(k, 0), "txt (scaled)"
    else:
        img = _read_pixels(path)
        guess = fh - int(round(fw * 3 / 4))
        if guess > 0 and detect_strip(img, expected=guess):
            t.strip_px, t.strip_source = guess, "4:3 rule (detected)"
        elif (k := detect_strip(img)):
            t.strip_px, t.strip_source = k, "detected"
        else:
            t.strip_px, t.strip_source = 0, "none"
            if guess > 0:
                t.warnings.append(f"no data bar found (4:3 rule would crop {guess} px); set strip_px manually if needed")
    t.height = fh - t.strip_px
    if t.pixel_size_um is None:
        t.warnings.append("no pixel size (no .txt): positions in px only")
    return t


def scan(folder: str | Path | None = None, files: list[str | Path] | None = None,
         strip_px: int | None = None) -> list[TileInfo]:
    """All images of a folder (or the given files) with metadata; .txt sidecars share the base name."""
    paths: list[Path] = []
    if files:
        paths = [Path(f) for f in files]
    elif folder:
        paths = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file())
    else:
        raise ValueError("give a folder or a list of files")
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"images not found: {missing}")
    return [read_tile(p, strip_px=strip_px) for p in paths]


def detector_counts(tiles: list[TileInfo]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tiles:
        out[t.detector_class] = out.get(t.detector_class, 0) + 1
    return out


def detector_detail(tiles: list[TileInfo]) -> dict[str, int]:
    """Counts per detector name (BED-C, SED, ...)."""
    out: dict[str, int] = {}
    for t in tiles:
        k = t.detector or "unknown"
        out[k] = out.get(k, 0) + 1
    return out


def _read_pixels(path: str | Path) -> np.ndarray:
    return bse_io.read_image(path)


# ---- parameters ------------------------------------------------------------------------------------
class StitchParams(BaseModel):
    """Stitching of overlapping SEM images. Units: px, um."""
    folder: str | None = None
    files: list[str] | None = None
    detector: Literal["BSE", "SE", "both"] = "BSE"
    se_layout: Literal["own", "bse"] = Field(
        "own", description="'bse': stitch BSE, register each SE tile to its BSE tile (same $SM_IMAGE_NUMBER, small "
                           "shift only) and place it with the BSE layout, so both mosaics are aligned")
    strip_px: int | None = Field(None, description="manual data-bar height (default: from the .txt / detected)")
    allow_rescale: bool = Field(False, description="rescale tiles with another pixel size to the finest one")
    stage_map: Literal["auto", "default"] | tuple[tuple[int, int], tuple[int, int]] = Field(
        "auto", description="stage -> image axes: auto (chosen from the data), default (measured JSM-IT500HR "
                            "mapping), or a 2x2 signed permutation ((col<-X, col<-Y), (row<-X, row<-Y))")
    search_um: float = Field(3.0, description="stage uncertainty: max. distance of a match from the stage prediction")
    min_overlap_px: int = Field(48, description="minimum predicted overlap (width and height) for a pair")
    min_score: float = Field(0.3, description="NCC below this rejects a pair")
    se_min_score: float = Field(0.1, description="NCC below this rejects an SE->BSE match (cross-detector)")
    se_search_px: int = Field(40, description="max. SE->BSE shift searched")
    max_residual_px: float = Field(3.0, description="pairs with a larger residual after the global fit are dropped")
    rotation_check: bool = Field(False, description="also report the best small rotation (+-1 deg) per pair")
    normalize: bool | None = Field(None, description="per-tile gain/offset normalisation (default: on for feather, "
                                                     "off for blend='none')")
    blend: Literal["feather", "none"] = "feather"
    info_strip: Literal["none", "keep", "generate"] = "none"
    single_column_tol_px: int | None = Field(None, description="'keep' needs mosaic width - tile width <= this "
                                                               "(default 5 % of the tile width; the 8-tile test column drifts 58 px)")
    exclude: list[str] = Field([], description="tiles to leave out (file stem or image number)")
    nudge: dict[str, tuple[float, float]] = Field({}, description="manual shift (dx, dy) in px per tile (stem or "
                                                                  "image number), applied after the global fit")
    tiff_tile: int = 256
    pyramid_min_px: int = Field(8192, description="write a pyramid when the mosaic is larger than this (px)")
    preview_max_px: int = 2048
    band_px: int = 2048


# ---- pixels -----------------------------------------------------------------------------------------------
class TileStore:
    """Strip-free tile pixels (float32), loaded on demand with a small LRU (RAM bound for large sets)."""

    def __init__(self, tiles: list[TileInfo], scale: list[float] | None = None, max_items: int = 12,
                 max_small: int = 256, max_surfaces: int = 24):
        self.tiles = tiles
        self.scale = scale or [1.0] * len(tiles)
        self.cache: OrderedDict[tuple[int, int], np.ndarray] = OrderedDict()      # full resolution (LRU)
        self.small: OrderedDict[tuple[int, int], np.ndarray] = OrderedDict()      # downsampled
        self.spectra: OrderedDict[tuple, tuple] = OrderedDict()
        self.surfaces: OrderedDict[tuple, tuple] = OrderedDict()
        self.max_items, self.max_small, self.max_surfaces = max_items, max_small, max_surfaces

    @staticmethod
    def _put(cache: OrderedDict, key, val, limit: int):
        cache[key] = val
        while len(cache) > limit:
            cache.popitem(last=False)
        return val

    def surface(self, i: int, j: int, f: int) -> tuple[np.ndarray, np.ndarray]:
        """Masked NCC surface of tiles i, j at 1/f resolution (cached; independent of any stage prediction)."""
        key = (i, j, f)
        if key in self.surfaces:
            self.surfaces.move_to_end(key)
            return self.surfaces[key]
        A, B = self.get(i, f), self.get(j, f)
        fshape = _fft_shape(A.shape, B.shape)
        sa = self._spec(i, f, fshape, flip=False)
        sb = self._spec(j, f, fshape, flip=True)
        return self._put(self.surfaces, key, ncc_surface(A, B, sa, sb), self.max_surfaces)

    def _spec(self, i: int, f: int, fshape, flip: bool):
        key = (i, f, fshape, flip)
        if key in self.spectra:
            self.spectra.move_to_end(key)
            return self.spectra[key]
        return self._put(self.spectra, key, _spectra(self.get(i, f), fshape, flip), 4 * self.max_surfaces)

    def get(self, i: int, f: int = 1) -> np.ndarray:
        key = (i, f)
        c = self.cache if f == 1 else self.small
        if key in c:
            c.move_to_end(key)
            return c[key]
        if f == 1:
            t = self.tiles[i]
            a = _read_pixels(t.path)[: t.height].astype(np.float32)
            if self.scale[i] != 1.0:
                from skimage.transform import rescale  # noqa: PLC0415

                a = rescale(a, self.scale[i], order=1, preserve_range=True, anti_aliasing=True).astype(np.float32)
            return self._put(self.cache, key, a, self.max_items)
        return self._put(self.small, key, _downscale(self.get(i, 1), f), self.max_small)

    def shape(self, i: int) -> tuple[int, int]:
        t = self.tiles[i]
        s = self.scale[i]
        return (int(round(t.height * s)), int(round(t.width * s))) if s != 1.0 else (t.height, t.width)


def _downscale(a: np.ndarray, f: int) -> np.ndarray:
    if f <= 1:
        return a
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3), dtype=np.float32)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def _overlap(sa: tuple[int, int], sb: tuple[int, int], d: tuple[float, float]):
    """Overlap of tile A (at 0,0) and tile B at offset d = (dy, dx): slices in A and in B, or None."""
    dy, dx = int(round(d[0])), int(round(d[1]))
    y0, y1 = max(0, dy), min(sa[0], sb[0] + dy)
    x0, x1 = max(0, dx), min(sa[1], sb[1] + dx)
    if y1 <= y0 or x1 <= x0:
        return None
    return (slice(y0, y1), slice(x0, x1)), (slice(y0 - dy, y1 - dy), slice(x0 - dx, x1 - dx))


def ncc_at(A: np.ndarray, B: np.ndarray, d: tuple[float, float], min_px: int = 16, stride: int = 1) -> tuple[float, int]:
    """NCC of the overlap of A and B (B at offset d), and the overlap area in px."""
    ov = _overlap(A.shape, B.shape, d)
    if ov is None:
        return -1.0, 0
    (ya, xa), (yb, xb) = ov
    h, w = ya.stop - ya.start, xa.stop - xa.start
    if h < min_px or w < min_px:
        return -1.0, h * w
    return ncc(A[ya, xa][::stride, ::stride], B[yb, xb][::stride, ::stride]), h * w


def _hann2(shape) -> np.ndarray:
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1])).astype(np.float32)


def _pc_peaks(a: np.ndarray, b: np.ndarray, n: int = 6) -> list[tuple[int, int]]:
    """Strongest peaks of the phase-correlation surface of two equal-size windows (shift of b relative to a,
    modulo the window size)."""
    wa = (a - a.mean()) * _hann2(a.shape)
    wb = (b - b.mean()) * _hann2(b.shape)
    F = np.fft.fft2(wa) * np.conj(np.fft.fft2(wb))
    F /= np.abs(F) + 1e-9
    r = np.fft.ifft2(F).real
    from scipy.ndimage import maximum_filter  # noqa: PLC0415

    m = (r == maximum_filter(r, size=5, mode="wrap"))
    idx = np.argsort(r[m])[::-1][:n]
    ys, xs = np.nonzero(m)
    return [(int(ys[k]), int(xs[k])) for k in idx]


def _fft_shape(sa, sb) -> tuple[int, int]:
    from scipy.fft import next_fast_len  # noqa: PLC0415

    return (next_fast_len(sa[0] + sb[0] - 1, True), next_fast_len(sa[1] + sb[1] - 1, True))


def _spectra(A: np.ndarray, fshape, flip: bool):
    """rFFTs of the zero-mean image, its square and its support (flipped for the moving image)."""
    from scipy.fft import rfft2  # noqa: PLC0415

    a = A.astype(np.float64) - float(A.mean())
    if flip:
        a = a[::-1, ::-1]
    return rfft2(a, fshape), rfft2(a * a, fshape), rfft2(np.ones_like(a), fshape), A.shape


def ncc_surface(A: np.ndarray, B: np.ndarray, sa=None, sb=None) -> tuple[np.ndarray, np.ndarray]:
    """NCC of A and B for every integer offset of B's origin in A's frame, normalised over the overlapping pixels only
    (masked NCC, Padfield 2012, doi:10.1109/TIP.2011.2181402), via FFT. Returns (ncc, overlap area) as float32;
    index [sy + hb - 1, sx + wb - 1] holds offset (sy, sx). sa / sb: precomputed _spectra (sb flipped)."""
    from scipy.fft import irfft2  # noqa: PLC0415

    fshape = _fft_shape(A.shape, B.shape)
    sa = sa or _spectra(A, fshape, False)
    sb = sb or _spectra(B, fshape, True)
    FA, FA2, FOA, _ = sa
    FB, FB2, FOB, _ = sb
    out = (A.shape[0] + B.shape[0] - 1, A.shape[1] + B.shape[1] - 1)

    def inv(x):
        return irfft2(x, fshape)[: out[0], : out[1]]
    N = np.rint(inv(FOA * FOB))
    C, SA, SA2 = inv(FA * FB), inv(FA * FOB), inv(FA2 * FOB)
    SB, SB2 = inv(FOA * FB), inv(FOA * FB2)
    Nn = np.maximum(N, 1)
    num = C - SA * SB / Nn
    den = np.sqrt(np.maximum(SA2 - SA * SA / Nn, 0) * np.maximum(SB2 - SB * SB / Nn, 0))
    ncc_s = np.where(den > 1e-9 * max(float(den.max()), 1e-30), num / np.maximum(den, 1e-30), 0.0)
    return ncc_s.astype(np.float32), N.astype(np.float32)


def _surface_best(ncc_s: np.ndarray, N: np.ndarray, shape_b, min_area: float, centre=None, rad=None,
                  min_side: int = 4, shape_a=None):
    """Best offset of an NCC surface, optionally within +-rad of centre; overlap area >= min_area and both overlap
    sides >= min_side."""
    hb, wb = shape_b
    ys = np.arange(ncc_s.shape[0])[:, None] - (hb - 1)
    xs = np.arange(ncc_s.shape[1])[None, :] - (wb - 1)
    ok = N >= min_area
    if shape_a is not None:
        oh = np.minimum(shape_a[0], hb + ys) - np.maximum(0, ys)
        ow = np.minimum(shape_a[1], wb + xs) - np.maximum(0, xs)
        ok &= (oh >= min_side) & (ow >= min_side)
    if centre is not None and rad is not None:
        ok &= (np.abs(ys - centre[0]) <= rad + 1) & (np.abs(xs - centre[1]) <= rad + 1)
    if not ok.any():
        return None
    v = np.where(ok, ncc_s, -2.0)
    k = np.unravel_index(int(np.argmax(v)), v.shape)
    return (int(k[0] - (hb - 1)), int(k[1] - (wb - 1)))


@dataclass
class PairResult:
    i: int
    j: int
    offset: tuple[float, float] = (0.0, 0.0)     # (dy, dx) of tile j relative to tile i, px
    predicted: tuple[float, float] | None = None
    score: float = -1.0
    overlap_px: int = 0
    ok: bool = False
    reason: str = ""
    rotation_deg: float | None = None
    residual_px: float | None = None
    used: bool = False


def register_pair(A: np.ndarray, B: np.ndarray, predicted: tuple[float, float] | None, search_px: float | None,
                  min_overlap_px: int = 48, coarse: int = 4, store: tuple | None = None,
                  coarse_only: bool = False) -> PairResult:
    """Translation of B relative to A (B's origin in A's frame, (dy, dx)). `predicted`/`search_px`: stage prior
    (None = search everything). A, B are full-resolution float images; `store` = (TileStore, i, j) to reuse cached
    downsampled tiles."""
    res = PairResult(-1, -1, predicted=predicted)
    f = max(1, coarse)
    if store:
        st, i, j = store
        Ac, Bc = st.get(i, f), st.get(j, f)
        if not coarse_only and A is None:
            A, B = st.get(i), st.get(j)
    else:
        Ac, Bc = _downscale(A, f), _downscale(B, f)
    cands: list[tuple[float, float]] = []
    min_c = max(4, min_overlap_px // f)
    if predicted is not None:
        pc = (predicted[0] / f, predicted[1] / f)
        rad = (search_px or 0) / f
        # window = predicted overlap, grown by the search radius where both tiles allow it
        ov = _overlap(Ac.shape, Bc.shape, pc)
        if ov is None:
            # predicted not to overlap at all: grow the prediction toward A by the search radius
            res.reason = "no predicted overlap"
            return res
        (ya, xa), (yb, xb) = ov
        a, b = Ac[ya, xa], Bc[yb, xb]
        if min(a.shape) < min_c:
            res.reason = "predicted overlap too small"
            return res
        H, W = a.shape
        for py, px in _pc_peaks(a, b):
            for dy in (py, py - H):
                for dx in (px, px - W):
                    # b(y) ~ a(y + s) with s = (dy, dx): B's origin moves by +s relative to the prediction
                    s = (round(pc[0]) - dy, round(pc[1]) - dx)
                    if rad and (abs(s[0] - pc[0]) > rad + 1 or abs(s[1] - pc[1]) > rad + 1):
                        continue
                    cands.append(s)
        # plus the best NCC offset within the search window (robust where the phase-correlation peak is not)
        S, N = st.surface(i, j, f) if store else ncc_surface(Ac, Bc)
        sb = _surface_best(S, N, Bc.shape, min_c * min_c, centre=pc, rad=rad or None, min_side=min_c,
                           shape_a=Ac.shape)
        if sb is not None:
            cands.append(sb)
        if not cands:
            cands = [(round(pc[0]), round(pc[1]))]
    else:
        # no prior: whole tiles, padded to a common size
        H, W = max(Ac.shape[0], Bc.shape[0]), max(Ac.shape[1], Bc.shape[1])
        a = np.zeros((H, W), np.float32)
        b = np.zeros((H, W), np.float32)
        a[: Ac.shape[0], : Ac.shape[1]] = Ac - Ac.mean()
        b[: Bc.shape[0], : Bc.shape[1]] = Bc - Bc.mean()
        for py, px in _pc_peaks(a, b, n=8):
            for dy in (py, py - H):
                for dx in (px, px - W):
                    cands.append((-dy, -dx))
        S, N = st.surface(i, j, f) if store else ncc_surface(Ac, Bc)
        area = 0.05 * min(Ac.size, Bc.size)                   # no prior: at least 5 % overlap
        sb = _surface_best(S, N, Bc.shape, area, min_side=min_c, shape_a=Ac.shape)
        if sb is not None:
            cands.append(sb)
        min_area_no_prior = area
    best = (-2.0, None, 0)
    for s in dict.fromkeys(cands):
        sc, area = ncc_at(Ac, Bc, s, min_px=min_c)
        if predicted is None and area < min_area_no_prior:
            continue
        if sc > best[0]:
            best = (sc, s, area)
    if best[1] is None:
        res.reason = "no candidate"
        return res
    d0 = (best[1][0] * f, best[1][1] * f)
    if coarse_only:
        res.offset, res.score, res.overlap_px, res.ok = (float(d0[0]), float(d0[1])), float(best[0]), int(best[2]), True
        return res
    # full resolution: phase correlation of the overlap at the coarse match finds the remaining (< f px) shift
    # with subpixel precision (skimage phase_cross_correlation, upsample 20)
    stride = 2 if A.size > 2_000_000 else 1
    d = d0
    ov = _overlap(A.shape, B.shape, d)
    if ov is None:
        res.reason = "no overlap at the match"
        return res
    (ya, xa), (yb, xb) = ov
    from skimage.registration import phase_cross_correlation  # noqa: PLC0415

    sub, _, _ = phase_cross_correlation(A[ya, xa], B[yb, xb], upsample_factor=20, normalization=None)
    # sub = shift that registers B's window onto A's: B_win(y) ~ A_win(y + sub) -> B's origin at d + sub
    if np.all(np.abs(sub) <= f + 1):
        d1 = (d[0] + float(sub[0]), d[1] + float(sub[1]))
        if ncc_at(A, B, d1, min_px=min_overlap_px, stride=2 * stride)[0] >=                 ncc_at(A, B, d, min_px=min_overlap_px, stride=2 * stride)[0]:
            d = d1
    sc, area = ncc_at(A, B, d, min_px=min_overlap_px, stride=stride)
    res.offset, res.score, res.overlap_px = (float(d[0]), float(d[1])), float(sc), int(area)
    if predicted is not None and search_px:
        dist = math.hypot(d[0] - predicted[0], d[1] - predicted[1])
        if dist > search_px * math.sqrt(2) + 2:
            res.reason = f"{dist:.0f} px from the stage prediction"
            return res
    res.ok = True
    return res


def rotation_check(A: np.ndarray, B: np.ndarray, d: tuple[float, float], max_deg: float = 1.0,
                   step: float = 0.1) -> float:
    """Best small rotation of B about its centre (deg) by NCC on the overlap; report only."""
    from scipy.ndimage import rotate  # noqa: PLC0415

    ov = _overlap(A.shape, B.shape, d)
    if ov is None:
        return 0.0
    best = (-2.0, 0.0)
    for ang in np.arange(-max_deg, max_deg + 1e-9, step):
        Br = rotate(B, float(ang), reshape=False, order=1, mode="nearest") if abs(ang) > 1e-9 else B
        sc, _ = ncc_at(A, Br, d, stride=2)
        if sc > best[0]:
            best = (sc, float(ang))
    return round(best[1], 2)


# ---- stage prior -----------------------------------------------------------------------------------------
def _signed_perms() -> list[tuple[tuple[int, int], tuple[int, int]]]:
    out = []
    for swap in (False, True):
        for sx in (1, -1):
            for sy in (1, -1):
                out.append(((0, sy), (sx, 0)) if swap else ((sx, 0), (0, sy)))
    return out


def stage_um(t: TileInfo) -> np.ndarray:
    return np.array([t.stage_x_mm * 1000.0, t.stage_y_mm * 1000.0])


def predict_positions(tiles: list[TileInfo], M, ps_um: float) -> np.ndarray:
    """Tile origins (row, col) in px predicted from the stage with mapping M (rows (col, row), cols (X, Y))."""
    S = np.array([stage_um(t) for t in tiles])
    S = S - S[0]
    Mm = np.asarray(M, float) / ps_um
    cr = S @ Mm.T                     # (col, row)
    return np.stack([cr[:, 1], cr[:, 0]], axis=1)


def candidate_pairs(pos: np.ndarray, shapes: list[tuple[int, int]], min_overlap_px: int,
                    margin: float = 0.0) -> list[tuple[int, int]]:
    out = []
    for i, j in combinations(range(len(pos)), 2):
        d = pos[j] - pos[i]
        oh = min(shapes[i][0], shapes[j][0] + d[0]) - max(0, d[0])
        ow = min(shapes[i][1], shapes[j][1] + d[1]) - max(0, d[1])
        if oh + margin >= min_overlap_px and ow + margin >= min_overlap_px:
            out.append((i, j))
    return out


def choose_stage_map(tiles, store: TileStore, ps_um: float, p: StitchParams, coarse: int):
    """Try every signed axis permutation; the one whose predicted overlaps correlate best wins.
    Returns (M, report). Axes along which the stage did not move cannot be decided from the data: their
    mapping is taken from DEFAULT_STAGE_MAP and reported as 'default (stage did not move along X/Y)'."""
    S = np.array([stage_um(t) for t in tiles])
    span = S.max(axis=0) - S.min(axis=0)
    moved = span > 0.5                         # um (stage resolution 1 um)
    shapes = [store.shape(i) for i in range(len(tiles))]
    search_px = p.search_um / ps_um
    seen: dict[tuple, tuple] = {}
    report = []
    Md = np.array(DEFAULT_STAGE_MAP)
    for M in _signed_perms():
        Mx = np.array(M)
        if moved.sum() == 1:
            # the column of the unmoved axis does not change the prediction: give it the other image axis with the
            # default sign, so equivalent maps dedupe
            k = int(np.argmax(moved))
            a = int(np.argmax(np.abs(Mx[:, k])))
            Mx[:, 1 - k] = 0
            Mx[1 - a, 1 - k] = int(np.sign(Md[:, 1 - k].sum()))
        elif moved.sum() == 0:
            Mx = Md.copy()
        key = tuple(map(tuple, Mx.tolist()))
        if key in seen:
            continue
        pos = predict_positions(tiles, key, ps_um)
        pairs = candidate_pairs(pos, shapes, p.min_overlap_px)
        scores = []
        for i, j in pairs:
            r = register_pair(None, None, tuple(pos[j] - pos[i]), search_px, p.min_overlap_px, coarse,
                              store=(store, i, j), coarse_only=True)
            scores.append(max(r.score, 0.0) if r.ok else 0.0)
        # mean score over the pairs the mapping predicts; a mapping with no pairs scores 0
        total = float(np.mean(scores)) if scores else 0.0
        seen[key] = (total, len(pairs))
        report.append({"map": [list(r) for r in key], "pairs": len(pairs), "mean_score": round(total, 3)})
    best = max(seen.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
    notes = [f"stage did not move along {'XY'[k]}: that axis uses the default mapping" for k in range(2) if not moved[k]]
    return best, {"candidates": report, "chosen": [list(r) for r in best], "notes": notes,
                  "stage_span_um": [round(float(v), 1) for v in span]}


def _is_signed_perm(M) -> bool:
    M = np.abs(np.asarray(M))
    return bool((M.sum(axis=0) == 1).all() and (M.sum(axis=1) == 1).all())


# ---- global placement ----------------------------------------------------------------------------------------
def solve_positions(n: int, pairs: list[PairResult], prior: np.ndarray | None, prior_weight: float = 1e-4,
                    max_residual_px: float = 3.0) -> tuple[np.ndarray, list[PairResult], list[int]]:
    """Weighted least squares: p_j - p_i = offset_ij (weight = NCC score), p ~ prior (weak), iteratively dropping
    the pair with the largest residual while it exceeds max_residual_px. Returns positions (n x 2: row, col),
    the pairs (used / residual set) and the tiles placed by the stage prior only."""
    good = [r for r in pairs if r.ok]
    while True:
        rows, rhs, wts = [], [], []
        for r in good:
            row = np.zeros(n)
            row[r.j], row[r.i] = 1.0, -1.0
            rows.append(row)
            rhs.append(r.offset)
            wts.append(max(r.score, 0.05))
        linked = {r.i for r in good} | {r.j for r in good}
        roots = {min(c) for c in _components(n, good)}
        for k in range(n):
            row = np.zeros(n)
            row[k] = 1.0
            if prior is not None:
                rows.append(row)
                rhs.append(tuple(prior[k]))
                wts.append(prior_weight)
            elif k in roots:                           # no stage: fix the first tile of each component
                rows.append(row)
                rhs.append((0.0, 0.0))
                wts.append(1.0)
        A = np.array(rows)
        b = np.array(rhs, float)
        w = np.sqrt(np.array(wts))[:, None]
        P, *_ = np.linalg.lstsq(A * w, b * w, rcond=None)
        for r in good:
            r.residual_px = float(np.hypot(*(P[r.j] - P[r.i] - np.array(r.offset))))
        worst = max(good, key=lambda r: r.residual_px, default=None)
        if worst is None or worst.residual_px <= max_residual_px:
            break
        worst.ok = False
        worst.reason = f"residual {worst.residual_px:.1f} px > {max_residual_px} px after the global fit"
        good = [r for r in good if r is not worst]
    for r in good:
        r.used = True
    stage_only = [k for k in range(n) if k not in linked]
    return P, pairs, stage_only


def _components(n: int, pairs: list[PairResult]) -> list[set[int]]:
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for r in pairs:
        parent[find(r.i)] = find(r.j)
    comps: dict[int, set[int]] = {}
    for k in range(n):
        comps.setdefault(find(k), set()).add(k)
    return list(comps.values())


# ---- intensity normalisation --------------------------------------------------------------------------------
def intensity_correction(store: TileStore, pos: np.ndarray, pairs: list[PairResult]) -> list[tuple[float, float]]:
    """Gain a_k and offset b_k per tile so that overlaps agree: log(sigma_i a_i) = log(sigma_j a_j) and
    mu_i a_i + b_i = mu_j a_j + b_j in least squares, mean log a = 0 and mean b = 0 (keeps the overall level)."""
    n = len(pos)
    used = [r for r in pairs if r.used]
    if not used:
        return [(1.0, 0.0)] * n
    stats = []
    for r in used:
        d = pos[r.j] - pos[r.i]
        ov = _overlap(store.shape(r.i), store.shape(r.j), tuple(d))
        if ov is None:
            continue
        (ya, xa), (yb, xb) = ov
        a = store.get(r.i)[ya, xa][::2, ::2]
        b = store.get(r.j)[yb, xb][::2, ::2]
        stats.append((r.i, r.j, float(a.mean()), float(a.std()) + 1e-6, float(b.mean()), float(b.std()) + 1e-6))
    rows, rhs = [], []
    for i, j, mi, si, mj, sj in stats:
        row = np.zeros(n)
        row[i], row[j] = 1.0, -1.0
        rows.append(row)
        rhs.append(math.log(sj) - math.log(si))
    rows.append(np.ones(n))
    rhs.append(0.0)
    la, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    a = np.exp(la)
    rows, rhs = [], []
    for i, j, mi, si, mj, sj in stats:
        row = np.zeros(n)
        row[i], row[j] = 1.0, -1.0
        rows.append(row)
        rhs.append(mj * a[j] - mi * a[i])
    rows.append(np.ones(n))
    rhs.append(0.0)
    b, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    # keep each tile's mean level: offsets relative to the mean gain change
    mean_level = np.mean([store.get(k, 8).mean() for k in range(n)])
    b = b + mean_level * (1 - a.mean())
    return [(float(a[k]), float(b[k])) for k in range(n)]


# ---- rendering ----------------------------------------------------------------------------------------------
def _edge_weight(shape: tuple[int, int]) -> np.ndarray:
    """Linear feathering weight: distance (px) to the nearest tile edge, + 1."""
    h, w = shape
    wy = np.minimum(np.arange(h) + 1, h - np.arange(h)).astype(np.float32)
    wx = np.minimum(np.arange(w) + 1, w - np.arange(w)).astype(np.float32)
    return np.minimum(wy[:, None], wx[None, :])


def render(store: TileStore, origins: np.ndarray, canvas: tuple[int, int], out: np.ndarray,
           gains: list[tuple[float, float]] | None, blend: str, dtype, band_px: int = 2048,
           msg: str = "render", span: tuple[float, float] = (0.0, 1.0)) -> None:
    """Write the mosaic into `out` (canvas shape, may be a memmap) band by band. origins: integer (row, col)."""
    H, W = canvas
    info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
    n = len(origins)
    weights: dict[tuple[int, int], np.ndarray] = {}
    for y0 in range(0, H, band_px):
        y1 = min(H, y0 + band_px)
        acc = np.zeros((y1 - y0, W), np.float32)
        wsum = np.zeros((y1 - y0, W), np.float32)
        for k in range(n):
            th, tw = store.shape(k)
            r0, c0 = int(origins[k][0]), int(origins[k][1])
            a0, a1 = max(y0, r0), min(y1, r0 + th)
            if a1 <= a0:
                continue
            tile = store.get(k)[a0 - r0: a1 - r0]
            if gains:
                g, o = gains[k]
                tile = tile * g + o
            if (th, tw) not in weights:
                weights[(th, tw)] = _edge_weight((th, tw))
            wk = weights[(th, tw)][a0 - r0: a1 - r0]
            c1 = c0 + tw
            cs = slice(max(0, c0), min(W, c1))
            ts = slice(cs.start - c0, cs.stop - c0)
            sub_acc, sub_w = acc[a0 - y0: a1 - y0, cs], wsum[a0 - y0: a1 - y0, cs]
            if blend == "feather":
                sub_acc += tile[:, ts] * wk[:, ts]
                sub_w += wk[:, ts]
            else:                                   # winner takes all: the tile whose edge is farthest
                m = wk[:, ts] > sub_w
                sub_acc[m] = tile[:, ts][m]
                sub_w[m] = wk[:, ts][m]
        if blend == "feather":
            np.divide(acc, wsum, out=acc, where=wsum > 0)
        if info:
            np.clip(np.rint(acc), info.min, info.max, out=acc)
        out[y0:y1] = acc.astype(dtype)
        _progress(msg, span[0] + (span[1] - span[0]) * y1 / H)


# ---- output -------------------------------------------------------------------------------------------------
def write_tiff(path: Path, img: np.ndarray, ps_um: float | None, tile: int = 256, pyramid_min_px: int = 8192,
               description: dict | None = None) -> dict:
    """TIFF of the mosaic: tiled, zlib; BigTIFF when > 2 GB; pyramid (sub-IFDs, each level / 2) when larger than
    pyramid_min_px. Resolution tags in px/cm from the pixel size."""
    import tifffile  # noqa: PLC0415

    H, W = img.shape[:2]
    big = img.nbytes > 2**31 - 2**25
    levels = int(math.ceil(math.log2(max(H, W) / 1024))) if max(H, W) > pyramid_min_px else 0   # down to ~1024 px
    res = {} if not ps_um else {"resolution": (1e4 / ps_um, 1e4 / ps_um), "resolutionunit": "CENTIMETER"}
    with tifffile.TiffWriter(path, bigtiff=big) as tw:
        opts = dict(tile=(tile, tile), compression="zlib", photometric="minisblack", metadata=None, **res)
        tw.write(img, subifds=levels, description=json.dumps(description or {}), **opts)
        lev = img
        for lvl in range(1, levels + 1):
            lev = _downscale_band(lev)
            r = {} if not ps_um else {"resolution": (res["resolution"][0] / 2 ** lvl,) * 2,
                                      "resolutionunit": "CENTIMETER"}
            tw.write(lev, subfiletype=1, tile=(tile, tile), compression="zlib", photometric="minisblack",
                     metadata=None, **r)
    return {"bigtiff": big, "pyramid_levels": levels}


def _downscale_band(a: np.ndarray, band: int = 4096) -> np.ndarray:
    H, W = (a.shape[0] // 2) * 2, (a.shape[1] // 2) * 2
    out = np.empty((H // 2, W // 2), a.dtype)
    for y in range(0, H, band):
        y1 = min(H, y + band)
        out[y // 2: y1 // 2] = _downscale(a[y:y1, :W].astype(np.float32), 2).round().astype(a.dtype)
    return out


def write_preview(path: Path, img: np.ndarray, max_px: int = 2048) -> float:
    """PNG preview (block mean, longest side <= max_px). Returns the downscale factor."""
    from PIL import Image  # noqa: PLC0415

    f = max(1, math.ceil(max(img.shape[:2]) / max_px))
    small = img
    if f > 1:
        rows = []
        for y in range(0, (img.shape[0] // f) * f, 4096 // f * f or f):
            y1 = min((img.shape[0] // f) * f, y + (4096 // f * f or f))
            rows.append(_downscale(img[y:y1].astype(np.float32), f))
        small = np.concatenate(rows, axis=0)
    a = small.astype(np.float32)
    if img.dtype != np.uint8:
        lo, hi = np.percentile(a, (0.1, 99.9))
        a = (a - lo) / max(hi - lo, 1e-9) * 255
    Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8)).save(path, optimize=False)
    return float(f)


# ---- main --------------------------------------------------------------------------------------------------
@dataclass
class MosaicResult:
    detector: str
    tiff: Path
    preview: Path
    sidecar: Path
    table: dict
    info: dict = field(default_factory=dict)


def _match(t: TileInfo, keys: list[str] | dict) -> str | None:
    for k in keys:
        if k == t.name or (t.image_number is not None and str(t.image_number) == str(k)) or k == Path(t.path).name:
            return k
    return None


def check_consistency(tiles: list[TileInfo], allow_rescale: bool) -> tuple[list[float], list[str]]:
    """Refuse tiles with another pixel size, magnification, scan rotation, stage rotation / tilt or size (unless
    allow_rescale: different pixel sizes are rescaled to the finest). Returns per-tile scale and notes."""
    notes = []
    ps = [t.pixel_size_um for t in tiles]
    known = [p for p in ps if p]
    scale = [1.0] * len(tiles)

    def differs(vals, tol):
        v = [x for x in vals if x is not None]
        return bool(v) and (max(v) - min(v)) > tol

    problems = []
    if known and len(known) != len(ps):
        problems.append("some tiles have no pixel size (.txt missing)")
    if known and differs(known, 1e-4 * min(known)):
        problems.append(f"pixel sizes differ: {sorted({round(p * 1000, 3) for p in known})} nm")
    if differs([t.magnification for t in tiles], 1e-6):
        problems.append(f"magnifications differ: {sorted({t.magnification for t in tiles if t.magnification})}")
    for name, attr, tol in (("scan rotation", "scan_rotation_deg", 0.01), ("stage rotation", "stage_rotation_deg", 0.01),
                            ("stage tilt", "stage_tilt_deg", 0.01)):
        if differs([getattr(t, attr) for t in tiles], tol):
            vals = sorted({getattr(t, attr) for t in tiles if getattr(t, attr) is not None})
            raise ValueError(f"{name} differs between tiles ({vals} deg): tiles are rotated / foreshortened against "
                             "each other; stitch each setting separately")
    if problems:
        rescalable = all("pixel size" in p or "magnification" in p for p in problems) and len(known) == len(ps)
        if not (allow_rescale and rescalable):
            raise ValueError("tiles are not compatible: " + "; ".join(problems) +
                             (" (allow_rescale=True rescales them to the finest pixel size)" if rescalable else ""))
        fine = min(known)
        scale = [p / fine for p in ps]
        notes.append(f"rescaled to {fine * 1000:.3f} nm/px: " + "; ".join(problems))
    bs = {t.beam_shift for t in tiles if t.beam_shift is not None}
    if len(bs) > 1:
        notes.append("MP_BEAM_SHIFT differs between tiles; the stage prior ignores beam shift (registration "
                     "corrects it)")
    return scale, notes


def _register_layout(tiles: list[TileInfo], store: TileStore, p: StitchParams, label: str):
    """Pairwise registration + global placement. Returns positions (n x 2, row/col px, subpixel), pairs,
    stage-only tiles, stage-map report and the stage prediction."""
    n = len(tiles)
    shapes = [store.shape(i) for i in range(n)]
    ps = min(t.pixel_size_um for t in tiles) if all(t.pixel_size_um for t in tiles) else None
    coarse = max(1, int(round(max(shapes[0]) / 640)))
    use_stage = ps is not None and all(t.has_stage for t in tiles) and n > 1
    report: dict = {}
    prior = None
    if use_stage:
        _progress(f"{label}: stage mapping", 0.02)
        if p.stage_map == "auto":
            M, report = choose_stage_map(tiles, store, ps, p, coarse)
        elif p.stage_map == "default":
            M, report = DEFAULT_STAGE_MAP, {"chosen": [list(r) for r in DEFAULT_STAGE_MAP], "notes": ["default"]}
        else:
            if not _is_signed_perm(p.stage_map):
                raise ValueError("stage_map must be a signed axis permutation")
            M, report = p.stage_map, {"chosen": [list(r) for r in p.stage_map], "notes": ["given"]}
        prior = predict_positions(tiles, M, ps)
        report["matrix"] = [list(map(int, r)) for r in M]
        pair_ids = candidate_pairs(prior, shapes, p.min_overlap_px)
        search = p.search_um / ps
    else:
        report = {"notes": ["no stage positions / pixel size: all pairs tried"]}
        pair_ids = list(combinations(range(n), 2))
        search = None
    pairs: list[PairResult] = []
    for k, (i, j) in enumerate(pair_ids):
        _progress(f"{label}: pair {k + 1}/{len(pair_ids)}", 0.05 + 0.6 * k / max(1, len(pair_ids)))
        pred = tuple(prior[j] - prior[i]) if prior is not None else None
        r = register_pair(None, None, pred, search, p.min_overlap_px, coarse, store=(store, i, j))
        r.i, r.j = i, j
        if r.ok and r.score < p.min_score:
            r.ok, r.reason = False, f"NCC {r.score:.2f} < {p.min_score}"
        if r.ok or prior is not None:
            pairs.append(r)
        if p.rotation_check and r.ok:
            r.rotation_deg = rotation_check(store.get(i), store.get(j), r.offset)
    P, pairs, stage_only = solve_positions(n, pairs, prior, max_residual_px=p.max_residual_px)
    if prior is None and len(_components(n, [r for r in pairs if r.used])) > 1:
        raise ValueError("without stage data the tiles do not form one connected group of good matches; give the "
                         ".txt files or exclude unrelated images")
    return P, pairs, stage_only, report, prior


def _stage_transform(tiles: list[TileInfo], pos_px: np.ndarray, report: dict, ps: float | None) -> dict | None:
    """Affine stage (X, Y in mm) -> mosaic (col, row in px) fitted to the final tile origins; axes along which the
    stage did not move take the chosen signed-permutation mapping."""
    if ps is None or not all(t.has_stage for t in tiles):
        return None
    S = np.array([[t.stage_x_mm, t.stage_y_mm] for t in tiles])
    C = np.stack([pos_px[:, 1], pos_px[:, 0]], axis=1)          # col, row
    M0 = np.asarray(report.get("matrix", DEFAULT_STAGE_MAP), float) * 1000.0 / ps
    span = S.max(axis=0) - S.min(axis=0)
    moved = span > 0.0005
    M = M0.copy()
    if moved.all() and len(tiles) >= 3:
        A = np.c_[S, np.ones(len(S))]
        X, *_ = np.linalg.lstsq(A, C, rcond=None)
        M = X[:2].T
        off = X[2]
    else:
        # fit only the moved axis' column (scale + cross term) and the offset
        k = int(np.argmax(moved)) if moved.any() else None
        if k is not None and len(tiles) >= 2:
            A = np.c_[S[:, k], np.ones(len(S))]
            X, *_ = np.linalg.lstsq(A, C, rcond=None)
            M[:, k] = X[0]
            off = X[1] - S[:, 1 - k].mean() * M[:, 1 - k]
        else:
            off = C.mean(axis=0) - S.mean(axis=0) @ M.T
    pred = S @ M.T + off
    rms = float(np.sqrt(np.mean(np.sum((pred - C) ** 2, axis=1))))
    return {"description": "mosaic [col, row] px = matrix @ [stage X, stage Y] mm + offset (tile origin = "
                           "top-left pixel of the tile's strip-free image)",
            "matrix_px_per_mm": M.round(4).tolist(), "offset_px": off.round(3).tolist(), "rms_px": round(rms, 2),
            "axis_map": report.get("matrix"), "fitted_axes": ["XY"[k] for k in range(2) if moved[k]]}


def stitch(p: StitchParams, out_dir: Path, tiles: list[TileInfo] | None = None) -> dict[str, MosaicResult]:
    """Stitch the selected detector(s); writes <out_dir>/mosaic_<DET>.tif / _preview.png / .json per detector."""
    from . import jeol_strip  # noqa: PLC0415

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_tiles = tiles if tiles is not None else scan(p.folder, p.files, strip_px=p.strip_px)
    all_tiles = [t for t in all_tiles if not _match(t, p.exclude)]
    by_det = {d: [t for t in all_tiles if t.detector_class == d] for d in ("BSE", "SE")}
    wanted = ["BSE", "SE"] if p.detector == "both" else [p.detector]
    for d in wanted:
        if not by_det[d]:
            raise ValueError(f"no {d} images (detector counts: {detector_counts(all_tiles)})")
    normalize = p.normalize if p.normalize is not None else (p.blend == "feather")
    results: dict[str, MosaicResult] = {}
    bse_layout = None
    order = ["BSE", "SE"] if ("SE" in wanted and p.se_layout == "bse") else wanted
    for det in order:
        tl = sorted(by_det[det], key=lambda t: (t.image_number or 0, t.name))
        if not tl:
            raise ValueError(f"se_layout='bse' needs the BSE tiles in the selection (none found)")
        scale, notes = check_consistency(tl, p.allow_rescale)
        store = TileStore(tl, scale)
        ps = min(t.pixel_size_um for t in tl) if all(t.pixel_size_um for t in tl) else None
        se_from = None
        if det == "SE" and p.se_layout == "bse":
            se_from = bse_layout
            P, pairs, stage_only, report, prior, se_matches = _se_from_bse(tl, store, bse_layout, p)
        else:
            P, pairs, stage_only, report, prior = _register_layout(tl, store, p, det)
            se_matches = None
        nud = np.zeros_like(P)
        for k, t in enumerate(tl):
            key = _match(t, p.nudge)
            if key is not None:
                dx, dy = p.nudge[key]
                nud[k] = (dy, dx)
        if se_from is not None:                      # same canvas and origin as the BSE mosaic
            origin_shift = se_from["origin_shift"]
            Q = P + nud - origin_shift
            canvas = se_from["canvas"]
            O = np.rint(Q).astype(int)
        else:
            origin_shift = (P + nud).min(axis=0)
            Q = P + nud - origin_shift
            O = np.rint(Q).astype(int)
            shapes = [store.shape(i) for i in range(len(tl))]
            canvas = (int(max(O[k][0] + shapes[k][0] for k in range(len(tl)))),
                      int(max(O[k][1] + shapes[k][1] for k in range(len(tl)))))
        if det == "BSE":
            bse_layout = {"tiles": tl, "P": P + nud, "origin_shift": origin_shift, "canvas": canvas}
        if det not in wanted:
            continue
        _progress(f"{det}: normalisation", 0.7)
        gains = intensity_correction(store, Q, pairs) if normalize and pairs else None
        dtype = np.uint8 if all(_read_dtype(t) == np.uint8 for t in tl[:1]) else np.uint16
        mm_path = out_dir / f".mosaic_{det}.raw"
        mosaic = np.memmap(mm_path, dtype=dtype, mode="w+", shape=canvas)
        try:
            render(store, O, canvas, mosaic, gains, p.blend, dtype, p.band_px, msg=f"{det}: render",
                   span=(0.72, 0.9))
            strip_info = {"mode": p.info_strip, "height_px": 0}
            final = mosaic
            if p.info_strip != "none":
                strip = _make_strip(p, tl, O, canvas, ps, store, jeol_strip)
                strip_info.update(strip["info"])
                final = np.memmap(out_dir / f".mosaic_{det}_strip.raw", dtype=dtype, mode="w+",
                                  shape=(canvas[0] + strip["image"].shape[0], canvas[1]))
                final[: canvas[0]] = mosaic
                final[canvas[0]:] = strip["image"].astype(dtype)
            _progress(f"{det}: writing", 0.92)
            table = _tile_table(tl, P, Q, O, ps, pairs, stage_only, nud, gains, se_matches)
            side = _sidecar(p, det, tl, Q, O, ps, pairs, stage_only, report, prior, canvas, strip_info, notes,
                            gains, normalize, se_matches, table)
            tif = out_dir / f"mosaic_{det}.tif"
            side["tiff"] = write_tiff(tif, np.asarray(final), ps, p.tiff_tile, p.pyramid_min_px,
                                      description={"ti64app": "sem.stitch", "detector": det,
                                                   "pixel_size_um": ps, "strip_px": strip_info["height_px"]})
            prev = out_dir / f"mosaic_{det}_preview.png"
            side["preview_scale"] = write_preview(prev, np.asarray(final), p.preview_max_px)
            side = _json_safe(side)
            sc = out_dir / f"mosaic_{det}.json"
            sc.write_text(json.dumps(side, indent=1, ensure_ascii=False), encoding="utf-8")
            results[det] = MosaicResult(det, tif, prev, sc, table, side)
        finally:
            mosaic = final = None                    # release the memory maps before deleting their files
            for f in out_dir.glob(f".mosaic_{det}*.raw"):
                try:
                    f.unlink()
                except OSError:
                    pass
        _progress(f"{det}: done", 1.0)
    return results


def _json_safe(o):
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, np.generic):
        return _json_safe(o.item())
    return o


def _read_dtype(t: TileInfo):
    from PIL import Image  # noqa: PLC0415

    if Path(t.path).suffix.lower() in (".tif", ".tiff"):
        import tifffile  # noqa: PLC0415

        with tifffile.TiffFile(t.path) as tf:
            return np.dtype(tf.pages[0].dtype) if np.dtype(tf.pages[0].dtype).itemsize <= 2 else np.dtype(np.uint16)
    with Image.open(t.path) as im:
        return np.uint16 if im.mode in ("I;16", "I") else np.uint8


def _se_from_bse(se_tiles: list[TileInfo], store: TileStore, bse_layout: dict, p: StitchParams):
    """Place each SE tile with the BSE layout: SE origin = BSE origin + small SE->BSE shift (same image number)."""
    btiles = bse_layout["tiles"]
    bnum = {t.image_number: k for k, t in enumerate(btiles)}
    bstore = TileStore(btiles)
    P = np.zeros((len(se_tiles), 2))
    matches = []
    for k, t in enumerate(se_tiles):
        _progress(f"SE: match to BSE {k + 1}/{len(se_tiles)}", 0.05 + 0.6 * k / len(se_tiles))
        if t.image_number not in bnum:
            raise ValueError(f"SE tile {t.name}: no BSE tile with image number {t.image_number}")
        b = bnum[t.image_number]
        A, B = bstore.get(b), store.get(k)
        r = _small_shift(A, B, p.se_search_px)
        ok = r[0] >= p.se_min_score
        shift = (r[1], r[2]) if ok else (0.0, 0.0)
        P[k] = bse_layout["P"][b] + np.array(shift)
        matches.append({"se": t.name, "bse": btiles[b].name, "image_number": t.image_number,
                        "shift_px": [round(shift[1], 2), round(shift[0], 2)], "score": round(r[0], 3), "ok": ok,
                        "seconds_before_bse": _dt(t, btiles[b])})
    return P, [], [], {"notes": ["SE placed with the BSE layout"]}, None, matches


def _dt(a: TileInfo, b: TileInfo) -> float | None:
    try:
        ta = datetime.fromisoformat(f"{a.date}T{a.time}")
        tb = datetime.fromisoformat(f"{b.date}T{b.time}")
        return (tb - ta).total_seconds()
    except ValueError:
        return None


def _small_shift(A: np.ndarray, B: np.ndarray, search: int) -> tuple[float, float, float]:
    """Shift (dy, dx) of B's origin relative to A within +-search px (same stage position, other detector): NCC of
    the smoothed intensities (Gaussian sigma 2 px; SE and BSE share the topography / phase contrast at that scale,
    pixel noise does not) on the masked NCC surface at 1/2 resolution, then +-2 px at full resolution and subpixel
    phase correlation."""
    from scipy.ndimage import gaussian_filter  # noqa: PLC0415
    from skimage.registration import phase_cross_correlation  # noqa: PLC0415

    SA, SB = gaussian_filter(A, 2.0), gaussian_filter(B, 2.0)
    f = 2
    a, b = _downscale(SA, f), _downscale(SB, f)
    S, N = ncc_surface(a, b)
    s = max(1, search // f)
    d0 = _surface_best(S, N, b.shape, 0.5 * b.size, centre=(0, 0), rad=s) or (0, 0)
    d = (d0[0] * f, d0[1] * f)
    (ya, xa), (yb, xb) = _overlap(A.shape, B.shape, d)
    sub, _, _ = phase_cross_correlation(SA[ya, xa], SB[yb, xb], upsample_factor=20, normalization=None)
    if np.all(np.abs(sub) <= f + 1):
        d1 = (d[0] + float(sub[0]), d[1] + float(sub[1]))
        if ncc_at(SA, SB, d1, stride=2)[0] >= ncc_at(SA, SB, d, stride=2)[0]:
            d = d1
    sc, _ = ncc_at(SA, SB, d, stride=2)
    return float(sc), float(d[0]), float(d[1])


def is_single_column(O: np.ndarray, shapes: list[tuple[int, int]], canvas: tuple[int, int], tol_px: int) -> bool:
    return canvas[1] - max(s[1] for s in shapes) <= tol_px


def _make_strip(p: StitchParams, tl: list[TileInfo], O: np.ndarray, canvas, ps, store: TileStore, jeol_strip):
    shapes = [store.shape(i) for i in range(len(tl))]
    if p.info_strip == "keep":
        tol = p.single_column_tol_px if p.single_column_tol_px is not None else int(round(0.05 * shapes[0][1]))
        if not is_single_column(O, shapes, canvas, tol):
            raise ValueError(f"info_strip='keep' needs a single-column mosaic (mosaic width {canvas[1]} px vs tile "
                             f"width {shapes[0][1]} px, tolerance {tol} px); use info_strip='generate'")
        bottom = int(np.argmax([O[k][0] + shapes[k][0] for k in range(len(tl))]))
        t = tl[bottom]
        if t.strip_px <= 0:
            raise ValueError(f"tile {t.name} has no data bar to keep")
        strip = _read_pixels(t.path)[t.height:]
        img = np.zeros((strip.shape[0], canvas[1]), strip.dtype)
        c0 = int(O[bottom][1])
        w = min(strip.shape[1], canvas[1] - c0)
        img[:, c0: c0 + w] = strip[:, :w]
        return {"image": img, "info": {"height_px": int(strip.shape[0]), "source_tile": t.name,
                                       "image_number": t.image_number, "x_offset_px": c0,
                                       "note": "original JEOL data bar of the bottom tile (its image number and date)"}}
    fields = jeol_strip.mosaic_fields(tl, ps)
    h = max(t.strip_px for t in tl) or int(round(tl[0].width / 20))
    img, info = jeol_strip.render_strip(fields, width=canvas[1], height=h, pixel_size_um=ps,
                                        ref_width=tl[0].width, max_bar_px=jeol_strip.max_bar_px(tl))
    return {"image": img, "info": {"height_px": int(img.shape[0]), **info, "fields": fields}}


def _tile_table(tl, P, Q, O, ps, pairs, stage_only, nud, gains, se_matches) -> dict:
    best: dict[int, float] = {}
    for r in pairs:
        if r.used:
            for k in (r.i, r.j):
                best[k] = max(best.get(k, -1.0), r.score)
    rows = {"name": [], "image_number": [], "detector": [], "x_px": [], "y_px": [], "x_int_px": [], "y_int_px": [],
            "x_um": [], "y_um": [], "width_px": [], "height_px": [], "stage_x_mm": [], "stage_y_mm": [],
            "best_score": [], "placed_by": [], "nudge_dx_px": [], "nudge_dy_px": [], "gain": [], "offset": []}
    for k, t in enumerate(tl):
        rows["name"].append(t.name)
        rows["image_number"].append(t.image_number if t.image_number is not None else -1)
        rows["detector"].append(t.detector)
        rows["x_px"].append(round(float(Q[k][1]), 3))
        rows["y_px"].append(round(float(Q[k][0]), 3))
        rows["x_int_px"].append(int(O[k][1]))
        rows["y_int_px"].append(int(O[k][0]))
        rows["x_um"].append(round(float(Q[k][1]) * ps, 4) if ps else float("nan"))
        rows["y_um"].append(round(float(Q[k][0]) * ps, 4) if ps else float("nan"))
        rows["width_px"].append(t.width)
        rows["height_px"].append(t.height)
        rows["stage_x_mm"].append(t.stage_x_mm if t.stage_x_mm is not None else float("nan"))
        rows["stage_y_mm"].append(t.stage_y_mm if t.stage_y_mm is not None else float("nan"))
        if se_matches is not None:
            m = se_matches[k]
            rows["best_score"].append(m["score"])
            rows["placed_by"].append("BSE layout" + ("" if m["ok"] else " (SE match weak: no shift)"))
        else:
            rows["best_score"].append(round(best.get(k, float("nan")), 3))
            rows["placed_by"].append("stage only" if k in stage_only else "registration")
        rows["nudge_dx_px"].append(float(nud[k][1]))
        rows["nudge_dy_px"].append(float(nud[k][0]))
        rows["gain"].append(round(gains[k][0], 4) if gains else 1.0)
        rows["offset"].append(round(gains[k][1], 3) if gains else 0.0)
    return rows


def _sidecar(p, det, tl, Q, O, ps, pairs, stage_only, report, prior, canvas, strip_info, notes, gains, normalize,
             se_matches, table) -> dict:
    stage_tf = _stage_transform(tl, Q, report, ps) if se_matches is None else None
    res = [r.residual_px for r in pairs if r.used and r.residual_px is not None]
    vals = lambda attr: sorted({getattr(t, attr) for t in tl if getattr(t, attr) is not None})  # noqa: E731
    return {
        "detector": det,
        "detectors": sorted({t.detector for t in tl}),
        "n_tiles": len(tl),
        "inputs": [{"image": t.path, "txt": t.txt, "image_number": t.image_number} for t in tl],
        "pixel_size_um": ps,
        "mosaic_px": [canvas[1], canvas[0]],                 # width, height (strip-free)
        "mosaic_um": [canvas[1] * ps, canvas[0] * ps] if ps else None,
        "strip": strip_info,                                  # info strip appended below the analysis pixels
        "tile_data_bar_px": sorted({t.strip_px for t in tl}),
        "tile_data_bar_source": sorted({t.strip_source for t in tl}),
        "tiles": [{k: table[k][i] for k in table} for i in range(len(tl))],
        "pairs": [{"a": tl[r.i].name, "b": tl[r.j].name, "offset_px": [round(r.offset[1], 3), round(r.offset[0], 3)],
                   "predicted_px": ([round(r.predicted[1], 1), round(r.predicted[0], 1)] if r.predicted else None),
                   "score": round(r.score, 4), "overlap_px": r.overlap_px, "ok": r.ok, "used": r.used,
                   "residual_px": None if r.residual_px is None else round(r.residual_px, 3), "reason": r.reason,
                   "rotation_deg": r.rotation_deg} for r in pairs],
        "residual_px": {"max": round(max(res), 3) if res else None,
                        "rms": round(float(np.sqrt(np.mean(np.square(res)))), 3) if res else None},
        "stage_only_tiles": [tl[k].name for k in stage_only],
        "stage_map": report,
        "stage_to_mosaic": stage_tf,
        "se_to_bse": se_matches,
        "beam_shift": sorted({tuple(t.beam_shift) for t in tl if t.beam_shift}),
        "settings": {"magnification": vals("magnification"), "kV": vals("kv"), "WD_mm": vals("wd_mm"),
                     "scan_rotation_deg": vals("scan_rotation_deg"), "stage_rotation_deg": vals("stage_rotation_deg"),
                     "stage_tilt_deg": vals("stage_tilt_deg")},
        "blend": p.blend,
        "normalize": normalize,
        "placement": "integer px (no resampling); subpixel positions in tiles[].x_px / y_px",
        "notes": notes,
        "params": p.model_dump(mode="json"),
        "method": "phase correlation + NCC, global least squares (Preibisch 2009 doi:10.1093/bioinformatics/btp184; "
                  "Chalfoun 2017 doi:10.1038/s41598-017-04567-y)",
    }


def layout_preview(tiles: list[TileInfo], thumb_px: int = 256) -> dict:
    """Stage-predicted layout (default JSM-IT500HR mapping, no registration) for a thumbnail preview."""
    if not tiles:
        return {"tiles": [], "width": 0, "height": 0}
    ps = tiles[0].pixel_size_um
    if ps and all(t.has_stage for t in tiles):
        pos = predict_positions(tiles, DEFAULT_STAGE_MAP, ps)
        pos = pos - pos.min(axis=0)
        src = "stage"
    else:
        pos = np.array([[0.0, k * t.width * 1.05] for k, t in enumerate(tiles)])
        src = "none (no stage data: tiles in a row)"
    W = float(max(pos[k][1] + t.width for k, t in enumerate(tiles)))
    H = float(max(pos[k][0] + t.height for k, t in enumerate(tiles)))
    return {"source": src, "width": W, "height": H,
            "tiles": [{"name": t.name, "x": float(pos[k][1]), "y": float(pos[k][0]), "w": t.width, "h": t.height,
                       "image_number": t.image_number} for k, t in enumerate(tiles)]}


def thumbnail(t: TileInfo, max_px: int = 256) -> np.ndarray:
    a = _read_pixels(t.path)[: t.height]
    f = max(1, math.ceil(max(a.shape) / max_px))
    return _downscale(a.astype(np.float32), f).astype(np.uint8) if a.dtype == np.uint8 else _downscale(a.astype(np.float32), f)
