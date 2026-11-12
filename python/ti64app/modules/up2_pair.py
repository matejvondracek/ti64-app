""".up2 <-> .ang pairing by scan ID (phase 6.6 fix 4) and the comparison shown on the View tab.

Bytes 42-57 of a .up2 header hold the scan GUID, equal to '# SCANID' of the .ang exported from the same scan
(io.up2.find_paired_ang). The paired map's per-point values (IQ, CI, phase from the .ang text, PRIAS from its
<map>_prias.txt export) are taken at the overview's points (Overview.point_index), so the View tab can show them on
the same hexagonal cells and correlate the pattern quality with the map's IQ: a wrong pattern order (e.g. padding
patterns not skipped) shows up as a low correlation or a jump between row bands.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..io import up2

ANG_CHANNELS = {"ang_iq": "IQ of the paired .ang", "ang_ci": "CI of the paired .ang",
                "ang_prias": "PRIAS of the paired .ang"}


@dataclass
class Pair:
    ang: Path
    map_id: str | None            # the .ang's map ID if it is linked in the project
    scan_id: str


def paired(project, up2_file: str | Path) -> Pair | None:
    """The paired .ang of a .up2 (project files first, then the .up2's folder)."""
    files = [m for m in project.files() if m.kind == "ang"] if project is not None else []
    a = up2.find_paired_ang(up2_file, [m.file for m in files])
    if a is None:
        return None
    mid = next((m.id for m in files if os.path.normcase(str(Path(m.file).absolute())) ==
                os.path.normcase(str(a.absolute()))), None)
    return Pair(a, mid, up2.read_header(up2_file).scan_id)


_ANG: dict[tuple, dict[str, np.ndarray]] = {}


def ang_columns(ang: str | Path, cache_dir: str | Path | None = None) -> dict[str, np.ndarray]:
    """IQ, CI, phase of every data row of an .ang (file order; cached in memory and in <cache>/ang_cols/)."""
    ang = Path(ang)
    st = ang.stat()
    k = (os.path.normcase(str(ang.absolute())), st.st_size, st.st_mtime_ns)
    if k in _ANG:
        return _ANG[k]
    cf = (Path(cache_dir) / "ang_cols" / f"{ang.stem[:40]}_{hashlib.sha1(repr(k).encode()).hexdigest()[:12]}.npz"
          if cache_dir else None)
    if cf is not None and cf.is_file():
        try:
            with np.load(cf) as z:
                _ANG[k] = {n: z[n] for n in z.files}
                return _ANG[k]
        except Exception:  # noqa: BLE001
            pass
    import pandas as pd  # noqa: PLC0415

    a = pd.read_csv(ang, comment="#", sep=r"\s+", header=None, usecols=[5, 6, 7], engine="c",
                    encoding="latin-1").to_numpy()
    d = {"iq": a[:, 0].astype(np.float32), "ci": a[:, 1].astype(np.float32), "phase": a[:, 2].astype(np.int16)}
    _ANG[k] = d
    if cf is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_name(f".{cf.stem}.{os.getpid()}.tmp.npz")
            np.savez(tmp, **d)
            tmp.replace(cf)
        except OSError:
            pass
    return d


def channel_on_overview(ov, pair: Pair, channel: str, cache_dir=None) -> np.ndarray:
    """(rows, cols) float array of a paired-.ang channel at the overview's points (NaN where none)."""
    if channel == "ang_prias":
        from ..io import prias  # noqa: PLC0415

        files = prias.available(pair.ang)
        k = next((c for c in prias.CHANNEL_ORDER if c in files), next(iter(files), None))
        if k is None:
            raise KeyError(f"no PRIAS export next to {pair.ang.name} ({pair.ang.stem}_prias.txt)")
        v = prias.values(files[k], pair.ang, cache_dir)
    else:
        v = ang_columns(pair.ang, cache_dir)[channel[4:]].astype(np.float64)
    idx = ov.point_index()
    out = np.full(idx.shape, np.nan)
    ok = (idx >= 0) & (idx < v.size)
    out[ok] = v[idx[ok]]
    return out


def highpass(a: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """a minus its NaN-aware Gaussian blur: removes the smooth shading (detector geometry), which differs between the
    pattern quality and OIM's IQ (opposite trends across the map) and would dominate a plain correlation."""
    from scipy.ndimage import gaussian_filter  # noqa: PLC0415

    ok = np.isfinite(a)
    w = gaussian_filter(ok.astype(np.float64), sigma)
    b = gaussian_filter(np.where(ok, a, 0.0), sigma) / np.maximum(w, 1e-9)
    return np.where(ok, a - b, np.nan)


def compare(ov, pair: Pair, cache_dir=None, bands: int = 10, min_ci: float = 0.1) -> dict:
    """Correlation of the overview's pattern quality with the paired .ang IQ, both high-pass filtered, on the points
    that diffract (.ang CI >= min_ci; band-free regions such as pores score HIGH in the pattern quality but low in
    OIM's IQ): overall, per band of rows / columns (a seam or a shifted part shows up as a band that drops), the
    shift (rows, columns) of the overview against the map with the best correlation ((0, 0) = right order), and the
    correlation on the even / odd scan rows (a misplaced padding pattern lowers only the odd rows)."""
    A = highpass(np.asarray(ov.iq, np.float64))
    B = highpass(channel_on_overview(ov, pair, "ang_iq", cache_dir))
    ci = channel_on_overview(ov, pair, "ang_ci", cache_dir)
    ok = np.isfinite(A) & np.isfinite(B) & (ci >= min_ci)
    if ok.sum() < 50:
        return {"n": int(ok.sum()), "r": None, "rows": [], "cols": [], "shift": (0, 0), "even": None, "odd": None}

    def r(m, a=A, b=B):
        return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 50 else float("nan")
    R, C = A.shape
    ri, ci_ = np.arange(R)[:, None], np.arange(C)[None, :]
    rows = [r(ok & (ri * bands // R == k)) for k in range(bands)]
    cols = [r(ok & (ci_ * bands // C == k)) for k in range(bands)]
    best = None
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            a = A[2:R - 2, 2:C - 2]
            b = B[2 + dr:R - 2 + dr, 2 + dc:C - 2 + dc]
            m = ok[2:R - 2, 2:C - 2] & np.isfinite(b)
            v = r(m, a, b)
            if best is None or v > best[0]:
                best = (v, dr, dc)
    odd = (ri * ov.step) % 2 == 1
    return {"n": int(ok.sum()), "r": r(ok), "rows": rows, "cols": cols, "shift": (best[1], best[2]),
            "even": r(ok & ~odd), "odd": r(ok & odd) if ov.step % 2 == 1 else None}
