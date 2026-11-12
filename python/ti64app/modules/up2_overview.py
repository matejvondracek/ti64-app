"""Overview map of an EDAX .up2 pattern file without loading it: one streaming pass in chunks of scan rows computes,
per pattern, the pattern quality (IQ) and a virtual-BSE intensity, so the View tab can show a map and load single
patterns on click.

- **Pattern quality (IQ)**: Krieger Lassen (1994) frequency-inertia image quality, as
  kikuchipy.pattern.get_image_quality (pattern normalised to mean 0 / std 1, |FFT| weighted by the squared frequency
  vector, IQ = 1 - inertia / inertia_max), computed AFTER the background correction kikuchipy recommends before IQ:
  static background (mean pattern of ~4000 patterns spread over the file) subtracted, then dynamic background
  removed with a Gaussian high-pass (sigma = pattern width / 8, kikuchipy's default) applied in the Fourier domain
  (periodic borders instead of kikuchipy's spatial blur). Without the correction the raw EDAX patterns (~32 000
  counts of smooth background) give an IQ map dominated by the beam position on the detector. Computed with a real
  FFT: the |F(u, v)| = |F(-u, -v)| symmetry folds kikuchipy's full-spectrum weights onto the half spectrum exactly
  (without correction the tests match kikuchipy to 1e-4).
  N. C. Krieger Lassen, "Automated determination of crystal orientations from electron backscattering patterns",
  PhD thesis, DTU (1994); kikuchipy doi:10.5281/zenodo.3597646.
- **Virtual BSE**: mean raw intensity of the whole pattern (the detector as one virtual diode).
- **Subsampling** (`step`): every step-th scan row is read (rows are contiguous in the file, so skipped rows are
  never read) and every step-th pattern of a row is evaluated. step 2 (default) reads half the file and computes a
  quarter of the patterns; the overview pixel is then 2 scan steps. step 1 = full resolution. On a hex grid every
  other kept row starts step/2 points later, so the subsampled points form a hex grid again (step x the scan step)
  and the View tab draws them as hexagons like the EBSD viewer (phase 6.6 fix 4).
- **Hex files with padding** ('extra patterns' flag): io.up2 skips the padding pattern of each short row; the old
  reader did not, which shifted the rows and gave the diagonal seam (fix 4).
- **Cache**: <project>/cache/up2_overview/<file stem>_<key>_s<step>.npz, key = hash of the absolute path, file size,
  modification time and the algorithm version. A cancelled pass keeps the rows done and resumes from there.
- **Memory**: one batch of ~16 k patterns at a time (float32 + half-spectrum complex64, < 0.6 GB), the next batch is
  read in a second thread while the current one is computed.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..io import up2

VERSION = 3           # 3: hex padding skipped, hex-preserving subsampling (fix 4)
BATCH_PATTERNS = 16_000
BACKGROUND_SAMPLES = 4096
CHANNELS = {"iq": "pattern quality (IQ)", "vbse": "virtual BSE (mean intensity)"}


class Cancelled(Exception):
    """The pass was cancelled; the rows done so far are cached and the next pass resumes there."""


@dataclass
class Overview:
    iq: np.ndarray            # (rows, cols) float32, NaN = not computed yet / no pattern
    vbse: np.ndarray
    step: int                 # subsampling in scan steps
    rows_done: int            # overview rows computed (resume point)
    seconds: float            # compute time so far
    header: up2.Up2Header
    background: np.ndarray | None = None     # static background (mean pattern) used for the IQ

    @property
    def complete(self) -> bool:
        return self.rows_done >= self.iq.shape[0]

    @property
    def pixel_um(self) -> tuple[float, float]:
        return self.step * self.header.x_step_um, self.step * self.header.y_step_um

    def channel(self, name: str) -> np.ndarray:
        return self.iq if name == "iq" else self.vbse

    def row_offset(self, r) -> np.ndarray | int:
        """First scan column of overview row r (hex grids, even step: every other row starts step/2 later)."""
        return row_offset(self.header, self.step, r)

    def scan_index(self, r: int, c: int) -> tuple[int, int]:
        """Scan row / column of overview pixel (r, c)."""
        return r * self.step, c * self.step + int(self.row_offset(r))

    def points(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(r, c, x, y) of every overview pixel that is a scan point (x, y = scan-point centres in um, as in the .ang),
        overview row-major order."""
        h = self.header
        R, C = self.iq.shape
        r, c = np.meshgrid(np.arange(R), np.arange(C), indexing="ij")
        sr = r * self.step
        sc = c * self.step + self.row_offset(r)
        ok = sc < (h.n_cols - (sr % 2 if h.hex_grid else 0))
        r, c, sr, sc = r[ok], c[ok], sr[ok], sc[ok]
        x = (sc + np.where(h.hex_grid & (sr % 2 == 1), 0.5, 0.0)) * h.x_step_um
        return r, c, x, sr * h.y_step_um

    def point_index(self) -> np.ndarray:
        """(rows, cols) .ang data-row index of each overview pixel (-1 = none): pairs it with the .ang map."""
        h = self.header
        out = np.full(self.iq.shape, -1, np.int64)
        r, c, _, _ = self.points()
        sr, sc = r * self.step, c * self.step + self.row_offset(r)
        if h.hex_grid:
            start = (sr // 2) * (2 * h.n_cols - 1) + np.where(sr % 2 == 1, h.n_cols, 0)
        else:
            start = sr * h.n_cols
        out[r, c] = start + sc
        return out

    def image(self, name: str, flatten: bool = False) -> np.ndarray:
        """uint8 picture of a channel (0.5 / 99.5 % percentile stretch, not computed = black). `flatten` divides by a
        strongly blurred copy (sigma = 1/40 of the map), which removes the smooth shading from the detector geometry
        and leaves the microstructure contrast."""
        return to_grey(self.channel(name), flatten)


def to_grey(a: np.ndarray, flatten: bool = False) -> np.ndarray:
    """uint8 picture of a 2-D channel (0.5 / 99.5 % percentile stretch, NaN = black); `flatten` divides by a strongly
    blurred copy (sigma = 1/40 of the map) first. Used for the overview channels and the paired .ang's channels."""
    a = np.asarray(a, np.float64)
    ok = np.isfinite(a)
    if not ok.any():
        return np.zeros(a.shape, np.uint8)
    if flatten:
        from scipy.ndimage import gaussian_filter  # noqa: PLC0415

        sig = max(a.shape) / 40
        f = np.where(ok, a, 0).astype(np.float64)
        w = gaussian_filter(ok.astype(np.float64), sig, mode="nearest")
        b = gaussian_filter(f, sig, mode="nearest") / np.maximum(w, 1e-9)
        a = np.where(ok, a / np.where(np.abs(b) > 1e-12, b, 1.0), np.nan)
    lo, hi = np.percentile(a[ok], (0.5, 99.5))
    g = np.clip((np.nan_to_num(a, nan=lo) - lo) / max(float(hi - lo), 1e-12) * 255, 0, 255).astype(np.uint8)
    g[~ok] = 0
    return g


_LAYOUTS: dict[tuple, tuple] = {}


def cells(ov: Overview):
    """(CellLayout, r, c) of the overview's points: hexagonal cells on a hex scan (the subsampled points form a hex
    grid again), squares on a square scan - the cell model of the EBSD viewer (raster.py), so the View tab draws a
    .up2 overview with the same tile renderer. Cached per file and step."""
    from ..raster import cell_layout  # noqa: PLC0415

    h = ov.header
    k = (h.file_size, h.n_rows, h.n_cols, h.hex_grid, h.x_step_um, h.y_step_um, ov.step, ov.iq.shape)
    if k not in _LAYOUTS:
        r, c, x, y = ov.points()
        L = cell_layout(x, y, "hex" if h.hex_grid else "square", ov.step * h.x_step_um)
        if len(_LAYOUTS) > 4:
            _LAYOUTS.clear()
        _LAYOUTS[k] = (L, r, c)
    return _LAYOUTS[k]


def layer(ov: Overview, grey: np.ndarray, key: str):
    """layers.Layer of a 2-D uint8 picture of the overview (per overview pixel) on its cells."""
    from ..layers import Layer  # noqa: PLC0415

    L, r, c = cells(ov)
    g = grey[r, c]
    colors = np.empty((r.size, 4), np.uint8)
    colors[:, 0] = colors[:, 1] = colors[:, 2] = g
    colors[:, 3] = 255
    return Layer(key, colors, L, {})


def overview_shape(h: up2.Up2Header, step: int) -> tuple[int, int]:
    return (h.n_rows + step - 1) // step, (h.n_cols + step - 1) // step


def row_offset(h: up2.Up2Header, step: int, r):
    """First scan column of overview row r: step/2 on every other row of a hex grid (even step), else 0."""
    if h.hex_grid and step > 1 and step % 2 == 0:
        return (np.asarray(r) % 2) * (step // 2)
    return np.zeros_like(np.asarray(r)) if np.ndim(r) else 0


def cache_path(cache_dir: str | Path, file: str | Path, step: int) -> Path:
    f = Path(file).absolute()
    st = f.stat()
    key = hashlib.sha1(f"{os.path.normcase(str(f))}|{st.st_size}|{st.st_mtime_ns}|v{VERSION}".encode()).hexdigest()[:12]
    return Path(cache_dir) / "up2_overview" / f"{f.stem[:40]}_{key}_s{step}.npz"


def load_cached(cache_dir: str | Path, file: str | Path, step: int) -> Overview | None:
    p = cache_path(cache_dir, file, step)
    if not p.is_file():
        return None
    try:
        with np.load(p) as z:
            return Overview(z["iq"], z["vbse"], int(z["step"]), int(z["rows_done"]), float(z["seconds"]),
                            up2.read_header(file), z["background"] if "background" in z.files else None)
    except Exception:  # noqa: BLE001  (half-written / old cache: recompute)
        return None


def _save(ov: Overview, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.tmp.npz")
    extra = {"background": ov.background} if ov.background is not None else {}
    np.savez(tmp, iq=ov.iq, vbse=ov.vbse, step=ov.step, rows_done=ov.rows_done, seconds=ov.seconds, **extra)
    tmp.replace(path)


# ---- pattern quality ------------------------------------------------------------------------------------------
def _frequency_vectors(h: int, w: int) -> np.ndarray:
    """kikuchipy.pattern.fft_frequency_vectors (same off-by-one convention)."""
    lx = np.arange(w) + 1
    lx[w // 2:] -= w + 1
    ly = np.arange(h) + 1
    ly[h // 2:] -= h + 1
    return (ly[:, None] ** 2 + lx[None, :] ** 2 - 1).astype(np.float64)


_WEIGHTS: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, float]] = {}


def _half_weights(h: int, w: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Weights of the rfft half spectrum so that sum(S_half * W) = sum(S_full * fv) and sum(S_half * C) = sum(S_full)."""
    if (h, w) not in _WEIGHTS:
        fv = _frequency_vectors(h, w)
        nc = w // 2 + 1
        W = np.empty((h, nc))
        C = np.empty((h, nc))
        neg = (-np.arange(h)) % h
        for k in range(nc):
            m = (-k) % w
            if m == k:
                W[:, k], C[:, k] = fv[:, k], 1.0
            else:
                W[:, k], C[:, k] = fv[:, k] + fv[neg, m], 2.0
        _WEIGHTS[(h, w)] = (W.astype(np.float32), C.astype(np.float32), float(fv.sum() / (h * w)))
    return _WEIGHTS[(h, w)]


_HIGHPASS: dict[tuple[int, int, float], np.ndarray] = {}


def _highpass(h: int, w: int, sigma: float) -> np.ndarray:
    """Gain of 'pattern minus its Gaussian blur (sigma px)' on the rfft half spectrum."""
    k = (h, w, float(sigma))
    if k not in _HIGHPASS:
        fy = np.fft.fftfreq(h)[:, None]
        fx = np.fft.rfftfreq(w)[None, :]
        _HIGHPASS[k] = (1.0 - np.exp(-2 * np.pi ** 2 * sigma ** 2 * (fx ** 2 + fy ** 2))).astype(np.float32)
    return _HIGHPASS[k]


def static_background(file: str | Path, n: int = BACKGROUND_SAMPLES) -> np.ndarray:
    """Mean pattern of n patterns at evenly spaced file positions (each read on its own, ~0.5 s)."""
    h = up2.read_header(file)
    pts = np.unique(np.linspace(0, h.n_points - 1, min(n, h.n_points)).astype(np.int64))
    idx = up2.point_file_index(h, pts)                      # scan points only (no padding patterns)
    acc = np.zeros((h.pattern_height, h.pattern_width), np.float64)
    with open(file, "rb", buffering=0) as fh:
        for i in idx:
            fh.seek(h.data_offset + int(i) * h.pattern_bytes)
            acc += np.frombuffer(fh.read(h.pattern_bytes), "<u2").reshape(acc.shape)
    return (acc / len(idx)).astype(np.float32)


def corrected_pattern(pattern: np.ndarray, background: np.ndarray, sigma: float | None = None) -> np.ndarray:
    """One pattern for display: static background subtracted, then its Gaussian blur (dynamic background, sigma =
    width / 8 by default) - the same correction as for the pattern quality, with a spatial blur."""
    from scipy.ndimage import gaussian_filter  # noqa: PLC0415

    x = pattern.astype(np.float32) - background
    return x - gaussian_filter(x, sigma or pattern.shape[1] / 8, mode="nearest")


def pattern_quality(patterns: np.ndarray, background: np.ndarray | None = None,
                    sigma: float | None = None) -> np.ndarray:
    """IQ of a stack of patterns (n, h, w) -> (n,) float32 (kikuchipy definition, see module doc). `background` is
    subtracted first (static correction), `sigma` (px) removes the dynamic background by a Gaussian high-pass;
    without both the result equals kikuchipy.pattern.get_image_quality."""
    import scipy.fft  # noqa: PLC0415

    x = patterns.astype(np.float32)
    if background is not None:
        x -= background
    n, h, w = x.shape
    x -= x.mean(axis=(1, 2), keepdims=True)
    sd = x.std(axis=(1, 2), keepdims=True)
    flat = sd[:, 0, 0] <= 0
    x /= np.where(sd > 0, sd, 1.0)
    S = np.abs(scipy.fft.rfft2(x, workers=-1))
    if sigma:
        S *= _highpass(h, w, sigma)
    W, C, imax = _half_weights(h, w)
    num = np.einsum("nij,ij->n", S, W)
    den = np.einsum("nij,ij->n", S, C)
    with np.errstate(invalid="ignore", divide="ignore"):
        iq = 1.0 - num / den / imax
    iq[flat] = np.nan
    return iq.astype(np.float32)


# ---- the streaming pass -----------------------------------------------------------------------------------------
def compute(file: str | Path, cache_dir: str | Path, step: int = 2,
            progress: Callable[[float, str], None] | None = None,
            cancel: threading.Event | None = None) -> Overview:
    """Overview of one .up2 (cached; resumes a cancelled pass). progress(fraction, text) is called per batch;
    setting `cancel` stops after the current batch (raises Cancelled, the rows done are cached)."""
    step = max(1, int(step))
    h = up2.read_header(file)
    path = cache_path(cache_dir, file, step)
    ov = load_cached(cache_dir, file, step)
    if ov is not None and ov.complete:
        return ov
    if ov is None:
        shp = overview_shape(h, step)
        ov = Overview(np.full(shp, np.nan, np.float32), np.full(shp, np.nan, np.float32), step, 0, 0.0, h)
    if ov.background is None:
        if progress:
            progress(0.0, "static background (mean pattern)")
        ov.background = static_background(file)
    sigma = h.pattern_width / 8
    n_out = ov.iq.shape[0]
    per_row = max(1, (h.n_cols + step - 1) // step)
    rows_per_batch = max(1, BATCH_PATTERNS // per_row)
    pb = h.pattern_bytes

    def read_rows(r0: int, r1: int) -> list[np.ndarray]:
        out = []
        with open(file, "rb", buffering=0) as fh:
            for orow in range(r0, r1):
                srow = orow * step
                n = up2.row_length(h, srow)
                fh.seek(h.data_offset + up2.pattern_index(h, srow, 0) * pb)
                a = np.fromfile(fh, dtype="<u2", count=n * h.pattern_height * h.pattern_width)
                off = int(row_offset(h, step, orow))
                out.append(a.reshape(n, h.pattern_height, h.pattern_width)[off::step])
        return out

    t0 = time.perf_counter() - ov.seconds
    batches = [(r, min(r + rows_per_batch, n_out)) for r in range(ov.rows_done, n_out, rows_per_batch)]
    with ThreadPoolExecutor(1) as pool:
        fut = pool.submit(read_rows, *batches[0]) if batches else None
        for i, (r0, r1) in enumerate(batches):
            rows = fut.result()
            fut = pool.submit(read_rows, *batches[i + 1]) if i + 1 < len(batches) else None
            lens = [len(a) for a in rows]
            stack = np.concatenate(rows)
            iq = pattern_quality(stack, ov.background, sigma)
            vb = stack.reshape(len(stack), -1).mean(axis=1, dtype=np.float64).astype(np.float32)
            k = 0
            for orow, n in zip(range(r0, r1), lens):
                ov.iq[orow, :n], ov.vbse[orow, :n] = iq[k:k + n], vb[k:k + n]
                k += n
            del stack, rows
            ov.rows_done = r1
            ov.seconds = time.perf_counter() - t0
            if progress:
                el = ov.seconds
                fr = r1 / n_out
                eta = el / max(fr, 1e-9) * (1 - fr) if fr < 1 else 0
                progress(fr, f"scan row {min(r1 * step, h.n_rows)} of {h.n_rows}, {el:.0f} s, ~{eta:.0f} s left")
            if cancel is not None and cancel.is_set() and r1 < n_out:
                if fut is not None:
                    fut.cancel()
                _save(ov, path)
                raise Cancelled(f"cancelled after {r1} of {n_out} overview rows (kept; the next pass resumes)")
    _save(ov, path)
    return ov


# ---- jobs shared by GUI pages (one pass per file and step, re-opening the file attaches to it) ----------------------
@dataclass
class Job:
    key: str
    fraction: float = 0.0
    text: str = "starting"
    done: bool = False
    error: str | None = None
    result: Overview | None = None
    cancel: threading.Event = None  # type: ignore[assignment]


JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def start_job(file: str | Path, cache_dir: str | Path, step: int) -> Job:
    """Start (or return the running) background pass for file + step."""
    key = str(cache_path(cache_dir, file, step))
    with _JOBS_LOCK:
        j = JOBS.get(key)
        if j is not None and not j.done:
            return j
        j = Job(key, cancel=threading.Event())
        JOBS[key] = j

    def work():
        def prog(fr, txt):
            j.fraction, j.text = fr, txt
        try:
            j.result = compute(file, cache_dir, step, prog, j.cancel)
            j.fraction, j.text = 1.0, f"done in {j.result.seconds:.0f} s"
        except Cancelled as e:
            j.error = str(e)
        except Exception as e:  # noqa: BLE001
            j.error = f"{type(e).__name__}: {e}"
        finally:
            j.done = True
    threading.Thread(target=work, name=f"up2-overview-{Path(file).stem}", daemon=True).start()
    return j


def running_job(file: str | Path, cache_dir: str | Path, step: int) -> Job | None:
    j = JOBS.get(str(cache_path(cache_dir, file, step)))
    return j if j is not None and not j.done else None
