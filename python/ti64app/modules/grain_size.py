"""Grain-size analysis (phase 4), Python-only: consumes backend results (pixel partitions, grain tables,
MTEX geometry) and never calls a backend. Same code for both backends, so differences in the results come
only from the engines' partitions (conformance tests).

Rules (ISO 13067 / ASTM E2627; docs/decisions.md D14): grains below a minimum number of pixels and grains
touching the map edge are excluded from per-grain statistics; the method and the rules are reported with
every number. Line intercepts follow the ASTM E112 convention: a chord cut by the map edge (or ending at an
unassigned pixel) counts as half an intercept.

Contents
- moment_ellipse     per grain: ellipse with the same second area moments as the grain (pixel centres plus
                     each pixel cell's own moment), major / minor axis 2a / 2b and the long-axis angle
- GridIndex, chords  test lines across the map, sampled on the pixel grid -> chords (runs of one label)
- lath_intercepts    chords perpendicular to each lath's own long axis (laths grouped in angle bins)
- directional_intercepts  chords along one direction (prior-beta width / length vs the build direction)
- dist_stats, lognormal_pdf, hist_edges  distribution statistics with number- and area-weighted log-normal fits
- read_oim_grain_file  OIM Analysis grain file export (for validation; format not yet verified, D14)
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# ---- second-moment ellipse ----------------------------------------------------------------------


def cell_variance(step_um: float, grid: str) -> float:
    """Second moment per axis of ONE pixel cell about its centre (um^2): square s^2/12; regular hexagon with
    neighbour distance s (apothem s/2, circumradius s/sqrt3): 5 R^2 / 24 = 5 s^2 / 72 (isotropic)."""
    return step_um ** 2 * (5.0 / 72.0 if grid == "hex" else 1.0 / 12.0)


def moment_ellipse(x: np.ndarray, y: np.ndarray, labels: np.ndarray, ids: np.ndarray, step_um: float,
                   grid: str) -> dict[str, np.ndarray]:
    """Per grain id: n_px, centroid, and the ellipse with the same second area moments (major axis 2a,
    minor axis 2b, long-axis angle from +x toward +y in 0..180 deg). For an ellipse this returns the ellipse
    itself; for a w x L rectangle the minor axis is 2w/sqrt(3) = 1.155 w (documented in D14)."""
    ids = np.asarray(ids, np.int64)
    lab = np.asarray(labels, np.int64)
    m = lab > 0
    size = max(int(lab.max(initial=0)), int(ids.max(initial=0))) + 1
    x0, y0 = float(np.mean(x[m])) if m.any() else 0.0, float(np.mean(y[m])) if m.any() else 0.0
    xs, ys, g = x[m] - x0, y[m] - y0, lab[m]
    n = np.bincount(g, minlength=size).astype(float)
    sx, sy = np.bincount(g, xs, size), np.bincount(g, ys, size)
    sxx, syy, sxy = np.bincount(g, xs * xs, size), np.bincount(g, ys * ys, size), np.bincount(g, xs * ys, size)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my = sx / n, sy / n
        cv = cell_variance(step_um, grid)
        vxx = sxx / n - mx * mx + cv
        vyy = syy / n - my * my + cv
        vxy = sxy / n - mx * my
    tr, det = vxx + vyy, vxx * vyy - vxy * vxy
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 0)
    ang = np.degrees(0.5 * np.arctan2(2 * vxy, vxx - vyy)) % 180.0
    ang = np.where(ang > 180.0 - 1e-6, 0.0, ang)
    sel = ids
    return {"id": ids, "n_px": n[sel].astype(np.int64), "cx_um": mx[sel] + x0, "cy_um": my[sel] + y0,
            "major_um": 4 * np.sqrt(l1[sel]), "minor_um": 4 * np.sqrt(l2[sel]),     # 2a, a = 2 sqrt(lambda)
            "angle_deg": ang[sel]}


# ---- test lines on the pixel grid ---------------------------------------------------------------
class GridIndex:
    """Nearest-pixel lookup on the measurement grid (rows of constant y; hex rows offset by half a step).
    A point is assigned to the row nearest in y, then to the nearest pixel in x within that row (on a hex
    grid this is a brick-wall approximation of the hexagonal cells, error < step/4)."""

    def __init__(self, x: np.ndarray, y: np.ndarray, step_um: float):
        self.x, self.y, self.step = np.asarray(x, float), np.asarray(y, float), float(step_um)
        yu = np.unique(np.round(self.y, 5))
        self.dy = float(np.median(np.diff(yu))) if yu.size > 1 else self.step
        self.y0 = float(yu[0])
        row = np.rint((self.y - self.y0) / self.dy).astype(np.int64)
        nrow = int(row.max()) + 1
        self.x0 = np.full(nrow, np.inf)
        np.minimum.at(self.x0, row, self.x)
        col = np.rint((self.x - self.x0[row]) / self.step).astype(np.int64)
        self.table = np.full((nrow, int(col.max()) + 1), -1, np.int64)
        self.table[row, col] = np.arange(self.x.size)

    def lookup(self, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        r = np.rint((py - self.y0) / self.dy)
        ok = (r >= 0) & (r < self.table.shape[0])
        r = np.where(ok, r, 0).astype(np.int64)
        c = np.rint((px - self.x0[r]) / self.step)
        ok &= (c >= 0) & (c < self.table.shape[1]) & np.isfinite(c)
        c = np.where(ok, c, 0).astype(np.int64)
        return np.where(ok, self.table[r, c], -1)


def chords(gi: GridIndex, labels: np.ndarray, direction_deg: float, spacing_um: float, sample_um: float,
           count: np.ndarray | None = None, max_samples: int = 4_000_000) -> dict[str, np.ndarray]:
    """Chords of parallel test lines (direction from +x toward +y, spacing_um apart) with the regions of
    `labels` (per pixel, 0 = unassigned). A chord = run of samples with one label > 0; length = number of
    samples x sample_um. truncated: the run ends at the map edge or at an unassigned pixel (not at another
    grain). `count` (bool lookup by label) restricts which labels are recorded; other grains still end chords."""
    lab = np.asarray(labels, np.int64)
    t = np.radians(direction_deg)
    u, nrm = np.array([np.cos(t), np.sin(t)]), np.array([-np.sin(t), np.cos(t)])
    pu, pn = gi.x * u[0] + gi.y * u[1], gi.x * nrm[0] + gi.y * nrm[1]
    offs = np.arange(pn.min() + spacing_um / 2, pn.max(), spacing_um)
    ts = np.arange(pu.min() - 2 * sample_um, pu.max() + 2 * sample_um, sample_um)
    out = {"label": [], "length_um": [], "truncated": [], "line": []}
    if offs.size == 0 or ts.size == 0:
        return {k: np.zeros(0, float if k == "length_um" else (bool if k == "truncated" else np.int64)) for k in out}
    per = max(1, max_samples // ts.size)
    for a in range(0, offs.size, per):
        o = offs[a:a + per]
        px = o[:, None] * nrm[0] + ts[None, :] * u[0]
        py = o[:, None] * nrm[1] + ts[None, :] * u[1]
        idx = gi.lookup(px.ravel(), py.ravel()).reshape(px.shape)
        v = np.where(idx >= 0, lab[np.maximum(idx, 0)], -1)
        v = np.concatenate([np.full((v.shape[0], 1), -2), v, np.full((v.shape[0], 1), -2)], axis=1)
        flat = v.ravel()
        start = np.flatnonzero(np.r_[True, flat[1:] != flat[:-1]])
        lengths = np.diff(np.r_[start, flat.size])
        val = flat[start]
        prev = np.r_[-2, val[:-1]]
        nxt = np.r_[val[1:], -2]
        keep = val > 0
        if count is not None:
            keep &= count[np.clip(val, 0, count.size - 1)] & (val < count.size)
        out["label"].append(val[keep])
        out["length_um"].append(lengths[keep] * sample_um)
        out["truncated"].append((prev[keep] <= 0) | (nxt[keep] <= 0))
        out["line"].append(a + start[keep] // v.shape[1])
    return {k: np.concatenate(v) for k, v in out.items()}


def lath_intercepts(gi: GridIndex, labels: np.ndarray, lath_ids: np.ndarray, long_axis_deg: np.ndarray,
                    spacing_um: float, sample_um: float, bin_deg: float = 5.0) -> dict[str, np.ndarray]:
    """Lath thickness by linear intercept perpendicular to each lath's own long axis. Laths are grouped by
    long-axis angle in bins of bin_deg (direction error <= bin_deg/2, chord error <= 1 - cos(bin_deg/2));
    per bin one family of test lines perpendicular to the bin centre records chords of that bin's laths only.
    Truncated chords (map edge, unassigned pixels) are dropped. Returns per lath: MEDIAN chord (the lath
    thickness; robust to the short chords across the lath tips), mean chord, number of chords, and all
    chords (lath id, length) for the pooled mean intercept."""
    lath_ids = np.asarray(lath_ids, np.int64)
    size = max(int(np.max(labels, initial=0)), int(lath_ids.max(initial=0))) + 1
    nb = int(round(180.0 / bin_deg))
    b = np.floor(((np.asarray(long_axis_deg, float) + bin_deg / 2) % 180.0) / bin_deg).astype(int) % nb
    cl, cv = [], []
    for k in np.unique(b):
        count = np.zeros(size, bool)
        count[lath_ids[b == k]] = True
        c = chords(gi, labels, k * bin_deg + 90.0, spacing_um, sample_um, count)
        ok = ~c["truncated"]
        cl.append(c["label"][ok])
        cv.append(c["length_um"][ok])
    lab = np.concatenate(cl) if cl else np.zeros(0, np.int64)
    ln = np.concatenate(cv) if cv else np.zeros(0)
    s = np.bincount(lab, ln, size)
    n = np.bincount(lab, minlength=size)
    mean = np.divide(s, n, out=np.full(size, np.nan), where=n > 0)
    return {"id": lath_ids, "median_chord_um": per_label_median(lab, ln, lath_ids),
            "mean_chord_um": mean[lath_ids], "n_chords": n[lath_ids], "chord_label": lab, "chord_um": ln}


def directional_intercepts(gi: GridIndex, labels: np.ndarray, direction_deg: float, spacing_um: float,
                           sample_um: float, count: np.ndarray | None = None) -> dict:
    """Chords of the labelled regions (only labels with count[label], if given) along one direction + the
    ASTM E112 mean intercept length L = (total chord length) / (N_complete + 0.5 N_truncated)."""
    c = chords(gi, labels, direction_deg, spacing_um, sample_um, count)
    tr = c["truncated"]
    n_eff = (~tr).sum() + 0.5 * tr.sum()
    mean_e112 = float(c["length_um"].sum() / n_eff) if n_eff > 0 else None
    return {**c, "mean_e112_um": mean_e112, "n_complete": int((~tr).sum()), "n_truncated": int(tr.sum()),
            "direction_deg": float(direction_deg % 180.0)}


def per_label_mean(label: np.ndarray, length: np.ndarray, ids: np.ndarray) -> np.ndarray:
    size = max(int(np.max(label, initial=0)), int(np.max(ids, initial=0))) + 1
    s = np.bincount(label, length, size)
    n = np.bincount(label, minlength=size)
    return np.divide(s, n, out=np.full(size, np.nan), where=n > 0)[np.asarray(ids, np.int64)]


def per_label_median(label: np.ndarray, length: np.ndarray, ids: np.ndarray) -> np.ndarray:
    ids = np.asarray(ids, np.int64)
    out = np.full(ids.size, np.nan)
    if label.size == 0:
        return out
    order = np.lexsort((length, label))
    lab, ln = label[order], length[order]
    start = np.flatnonzero(np.r_[True, lab[1:] != lab[:-1]])
    cnt = np.diff(np.r_[start, lab.size])
    lo, hi = start + (cnt - 1) // 2, start + cnt // 2
    med = 0.5 * (ln[lo] + ln[hi])
    pos = {int(k): i for i, k in enumerate(ids)}
    for k, m in zip(lab[start].tolist(), med.tolist()):
        if k in pos:
            out[pos[k]] = m
    return out


# ---- statistics ---------------------------------------------------------------------------------
def dist_stats(v: np.ndarray, w: np.ndarray | None = None) -> dict | None:
    """Number-weighted statistics of positive values v (+ area-weighted ones with weights w): n, mean, sd,
    median, p10, p90; log-normal fits by maximum likelihood (mu, sigma of ln v; number-weighted with KS test,
    p approximate because the parameters are fitted; area-weighted = weighted MLE with the weighted KS
    distance). Returns None if there are no values."""
    from scipy import stats as st  # noqa: PLC0415

    v = np.asarray(v, float)
    ok = np.isfinite(v) & (v > 0)
    if w is not None:
        w = np.asarray(w, float)
        ok &= np.isfinite(w) & (w > 0)
    v = v[ok]
    if v.size == 0:
        return None
    lv = np.log(v)
    mu, sg = float(lv.mean()), float(lv.std())
    out = {"n": int(v.size), "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if v.size > 1 else 0.0,
           "median": float(np.median(v)), "p10": float(np.percentile(v, 10)), "p90": float(np.percentile(v, 90)),
           "min": float(v.min()), "max": float(v.max()),
           "lognormal": {"mu": mu, "sigma": sg, "median": float(np.exp(mu)), "mean": float(np.exp(mu + sg ** 2 / 2))}}
    if v.size >= 3 and sg > 0:
        ks = st.kstest(v, "lognorm", args=(sg, 0, np.exp(mu)))
        out["lognormal"].update(ks_D=float(ks.statistic), ks_p=float(ks.pvalue))
    if w is not None:
        w = w[ok]
        ww = w / w.sum()
        mw = float((ww * lv).sum())
        sw = float(np.sqrt((ww * (lv - mw) ** 2).sum()))
        order = np.argsort(v)
        ecdf = np.cumsum(ww[order])
        D = None
        if sw > 0:
            F = st.norm.cdf((lv[order] - mw) / sw)
            D = float(np.max(np.maximum(np.abs(ecdf - F), np.abs(ecdf - ww[order] - F))))
        out["area_weighted"] = {"mean": float((ww * v).sum()),
                                "median": float(v[order][np.searchsorted(ecdf, 0.5)]),
                                "lognormal": {"mu": mw, "sigma": sw, "median": float(np.exp(mw)),
                                              "mean": float(np.exp(mw + sw ** 2 / 2)), "ks_D": D}}
    return out


def lognormal_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    x = np.asarray(x, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.exp(-(np.log(x) - mu) ** 2 / (2 * sigma ** 2)) / (x * sigma * np.sqrt(2 * np.pi))
    return np.where(x > 0, p, 0.0)


def hist_edges(v: np.ndarray, max_bins: int = 40) -> np.ndarray:
    """Bin edges from 0 to the 99.5th percentile (Freedman-Diaconis width, at most max_bins bins)."""
    v = np.asarray(v, float)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size < 2:
        return np.array([0.0, max(float(v.max(initial=1.0)), 1e-6) * 1.05])
    hi = float(np.percentile(v, 99.5)) * 1.02
    iqr = np.subtract(*np.percentile(v, [75, 25]))
    w = 2 * iqr / v.size ** (1 / 3) if iqr > 0 else hi / max_bins
    nb = int(np.clip(np.ceil(hi / w), 5, max_bins))
    return np.linspace(0.0, hi, nb + 1)


# ---- OIM grain file (validation) ------------------------------------------------------------------
OIM_KEYS = {  # neutral name -> regex on the '# Column k: description' header text (case-insensitive)
    "id": r"grain\s*id",
    "ecd_um": r"diameter",
    "area_um2": r"area",
    "n_px": r"(number of|#\s*of)\s*(measurement\s*)?points|grain size\s*\(#",
    "is_edge": r"edge",
    "phase": r"^phase",
    "ci": r"confidence",
}


def read_oim_grain_file(path: str | Path) -> dict:
    """Read an OIM Analysis grain file export (text, '# Column k: description' header lines, then numeric
    rows). Columns are found by keywords (OIM_KEYS); unknown columns are kept as col_k. NOT yet verified
    against a real export (docs/decisions.md D14): the matched header lines are returned for checking."""
    cols: dict[int, str] = {}
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            m = re.match(r"#\s*Column\s*(\d+)\s*(?:-\s*\d+)?\s*:\s*(.*)", s, re.I)
            if m:
                cols[int(m.group(1))] = m.group(2).strip()
            continue
        try:
            rows.append([float(t) for t in s.replace(",", " ").split()])
        except ValueError:
            continue
    if not rows:
        raise ValueError(f"no numeric rows in {path}")
    width = min(len(r) for r in rows)
    data = np.array([r[:width] for r in rows], float)
    out: dict = {"columns": cols, "matched": {}}
    for name, pat in OIM_KEYS.items():
        for k, desc in sorted(cols.items()):
            if k <= width and re.search(pat, desc, re.I):
                out[name] = data[:, k - 1]
                out["matched"][name] = f"column {k}: {desc}"
                break
    if "ecd_um" not in out and "area_um2" in out:
        out["ecd_um"] = 2 * np.sqrt(out["area_um2"] / np.pi)
        out["matched"]["ecd_um"] = "from area: 2 sqrt(A / pi)"
    if "ecd_um" not in out:
        raise ValueError(f"no grain diameter or area column found in {path}; header: {cols}")
    return out
