"""PRIAS images exported from OIM Analysis as text, next to the .ang (phase 6.6 fix 4).

The .ang exports declare PRIAS columns in the header but the rows hold only 10 columns, so PRIAS is exported
separately: <map>_prias.txt (Windows sometimes makes it <map>_prias.txt.txt), one file per channel:

    # Header: Project1::MVH1/JU04/Area 1/OIM Map 1::All data::Auto PRIAS Top   10 November2026
    # Column 1: x (Average for Grain)
    # Column 2: y (Average for Grain)
    # Column 3: Grayscale value - PRIAS Top Strip
    # Column 4: Color coded range - <none>
    # Column 5: Color coded value - <none>
    0.000 0.000 647.844 0 0                 (x y value 0 0, CRLF, one row per .ang point)

Points are matched to the .ang by position, not by row number: the .ang grid is rebuilt from its header (hex grid:
rows alternate NCOLS_ODD / NCOLS_EVEN points, odd rows shifted by half a step, origin = first data row) and every
PRIAS point goes to the grid point within half a step in x and y (the export rounds y to 3 decimals). A PRIAS file
whose point count or coordinates do not fit the map raises PriasMismatch with a plain-language reason.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

CHANNELS = {"top": "PRIAS Top Strip", "center": "PRIAS Center Square", "bottom": "PRIAS Bottom Strip"}
CHANNEL_ORDER = ("top", "center", "bottom")
_NAME = re.compile(r"_prias(?:[_ -][a-z]+)?\.txt(?:\.txt)?$", re.IGNORECASE)


class PriasMismatch(ValueError):
    """The PRIAS export does not fit the .ang (point count or coordinates)."""


def find_files(ang: str | Path) -> list[Path]:
    """PRIAS exports of a map in the .ang's folder: <stem>_prias.txt, <stem>_prias.txt.txt, <stem>_prias_top.txt ..."""
    ang = Path(ang)
    try:
        names = os.listdir(ang.parent)
    except OSError:
        return []
    stem = ang.stem.lower()
    out = [ang.parent / n for n in names
           if n.lower().startswith(stem) and _NAME.search(n) and n.lower()[len(stem):len(stem) + 6] == "_prias"]
    return sorted(out, key=lambda f: (CHANNEL_ORDER.index(channel(f)[0]) if channel(f)[0] in CHANNEL_ORDER else 9,
                                      f.name.lower()))


def is_prias_file(path: str | Path) -> bool:
    return bool(_NAME.search(Path(path).name))


def ang_of(path: str | Path) -> Path | None:
    """The .ang a PRIAS export belongs to (same folder, name before '_prias'), if it exists."""
    p = Path(path)
    i = p.name.lower().find("_prias")
    if i <= 0:
        return None
    for suf in (".ang", ".ANG"):
        a = p.parent / (p.name[:i] + suf)
        if a.is_file():
            return a
    return None


def read_header(path: str | Path) -> list[str]:
    out = []
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            out.append(line[1:].strip())
    return out


def channel(path: str | Path) -> tuple[str, str]:
    """(key, label) of the PRIAS channel from the header ('Column 3: Grayscale value - PRIAS Top Strip'):
    key top / center / bottom, else 'custom' with the header's own name."""
    label = ""
    try:
        for ln in read_header(path):
            m = re.match(r"Column 3:\s*(.*)", ln)
            if m:
                label = m.group(1).split(" - ", 1)[-1].strip()
                break
    except OSError:
        pass
    low = label.lower()
    for k in CHANNEL_ORDER:
        if k in low or (k == "center" and "centre" in low):
            return k, CHANNELS[k]
    return "custom", label or Path(path).name


def read_points(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """x, y (um), value of every data row (fast C parser; CRLF and '#' lines handled)."""
    import pandas as pd  # noqa: PLC0415

    df = pd.read_csv(path, comment="#", sep=r"\s+", header=None, usecols=[0, 1, 2], engine="c",
                     dtype=np.float64, skip_blank_lines=True, encoding="latin-1")
    a = df.to_numpy()
    return a[:, 0], a[:, 1], a[:, 2]


def _first_row_xy(ang: str | Path) -> tuple[float, float]:
    with open(ang, "r", encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("#") and line.strip():
                v = line.split()
                return float(v[3]), float(v[4])
    raise PriasMismatch(f"{Path(ang).name} has no data rows")


def grid_index(ang: str | Path, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, dict]:
    """File-order index in the .ang of each point (x, y), -1 = no grid point within half a step. Also returns the
    grid facts {n_points, step, ystep, hex, max_dx, max_dy}."""
    from .ang import read_header as ang_header  # noqa: PLC0415

    h = ang_header(ang)
    dx = float(h.fields["XSTEP"])
    dy = float(h.fields.get("YSTEP") or dx)
    nr = int(h.fields["NROWS"])
    no, ne = int(h.fields["NCOLS_ODD"]), int(h.fields.get("NCOLS_EVEN") or h.fields["NCOLS_ODD"])
    hexg = h.is_hex
    lens = np.array([(no if r % 2 == 0 else ne) if hexg else no for r in range(nr)], np.int64)
    starts = np.concatenate([[0], np.cumsum(lens)])
    x0, y0 = _first_row_xy(ang)
    r = np.round((y - y0) / dy).astype(np.int64)
    shift = np.where(hexg & (r % 2 == 1), dx / 2, 0.0)
    c = np.round((x - x0 - shift) / dx).astype(np.int64)
    ok = (r >= 0) & (r < nr)
    ok &= (c >= 0) & (c < lens[np.clip(r, 0, nr - 1)])
    ex = x0 + shift + c * dx
    ey = y0 + r * dy
    ddx, ddy = np.abs(x - ex), np.abs(y - ey)
    ok &= (ddx <= dx / 2 + 1e-9) & (ddy <= dy / 2 + 1e-9)
    idx = np.where(ok, starts[np.clip(r, 0, nr - 1)] + c, -1)
    facts = {"n_points": int(starts[-1]), "step": dx, "ystep": dy, "hex": hexg,
             "max_dx": float(ddx[ok].max()) if ok.any() else None, "max_dy": float(ddy[ok].max()) if ok.any() else None}
    return idx, facts


_CACHE: dict[tuple, tuple[np.ndarray, dict]] = {}


def _key(*files) -> tuple:
    out = []
    for f in files:
        st = Path(f).stat()
        out += [os.path.normcase(str(Path(f).absolute())), st.st_size, st.st_mtime_ns]
    return tuple(out)


def values(prias_file: str | Path, ang: str | Path, cache_dir: str | Path | None = None) -> np.ndarray:
    """PRIAS values in .ang FILE order (NaN where the export has no point). Raises PriasMismatch when the export does
    not fit the map. Cached in memory and, with cache_dir, as <cache_dir>/prias/<name>_<key>.npz."""
    k = _key(prias_file, ang)
    if k in _CACHE:
        return _CACHE[k][0]
    cf = None
    if cache_dir is not None:
        import hashlib  # noqa: PLC0415

        cf = Path(cache_dir) / "prias" / f"{Path(prias_file).stem[:40]}_{hashlib.sha1(repr(k).encode()).hexdigest()[:12]}.npz"
        if cf.is_file():
            try:
                with np.load(cf) as z:
                    v = z["values"]
                _CACHE[k] = (v, {})
                return v
            except Exception:  # noqa: BLE001
                pass
    v, info = _match(prias_file, ang)
    _CACHE[k] = (v, info)
    if cf is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_name(f".{cf.stem}.{os.getpid()}.tmp.npz")
            np.savez(tmp, values=v)
            tmp.replace(cf)
        except OSError:
            pass
    return v


def _match(prias_file, ang) -> tuple[np.ndarray, dict]:
    x, y, val = read_points(prias_file)
    idx, g = grid_index(ang, x, y)
    n, name, aname = g["n_points"], Path(prias_file).name, Path(ang).name
    bad = int((idx < 0).sum())
    if bad:
        i = int(np.argmax(idx < 0))
        raise PriasMismatch(f"{name}: {bad:,} of {x.size:,} points are not on the grid of {aname} (no map point "
                            f"within half a step; first at x {x[i]:g}, y {y[i]:g} µm). It was probably exported from "
                            "another map or a cropped / rotated one.")
    uniq = np.unique(idx)
    if uniq.size != idx.size:
        raise PriasMismatch(f"{name}: {idx.size - uniq.size:,} points fall on the same map point as another one "
                            f"(step {g['step']:g} µm of {aname} does not fit this export)")
    if x.size != n:
        raise PriasMismatch(f"{name} has {x.size:,} points, {aname} has {n:,}: export PRIAS from the same map with "
                            "'All data' (no partition / filter)")
    out = np.full(n, np.nan)
    out[idx] = val
    return out, {**g, "n": x.size}


def check(prias_file: str | Path, ang: str | Path) -> dict:
    """{ok, channel, message} for the map sheet: channel from the header, points matched to the .ang grid."""
    key, label = channel(prias_file)
    try:
        k = _key(prias_file, ang)
        if k not in _CACHE:
            _CACHE[k] = _match(prias_file, ang)
        v, info = _CACHE[k]
        n = int(np.isfinite(v).sum())
        return {"ok": True, "channel": label, "key": key,
                "message": f"{n:,} points, all on the map grid (within half a step)"}
    except PriasMismatch as e:
        return {"ok": False, "channel": label, "key": key, "message": str(e)}


def available(ang: str | Path) -> dict[str, Path]:
    """{channel key: file} of the PRIAS exports of a map (first file per channel)."""
    out: dict[str, Path] = {}
    for f in find_files(ang):
        k = channel(f)[0]
        out.setdefault(k, f)
    return out
