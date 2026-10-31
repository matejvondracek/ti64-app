"""Compare our IPF / CI maps with images exported from OIM Analysis (data/reference/).

Python-only module: works on backend RESULTS (per-pixel arrays in file order), never calls a backend.
Steps: raster our hex map (brick layout, like hexRaster) -> find the map area in the OIM image (crop
the uniform margins / legend) -> resize ours to that area -> score all 8 flips/rotations, so a
reference-frame mismatch (e.g. the x<->y swap of docs/decisions.md D1) shows up as a better score
for a non-identity transform.
Scores: IPF -> mean absolute RGB difference (0..1) and Pearson r of each channel;
        CI   -> Pearson r of the grey levels (OIM colour scales differ, so only the pattern is compared).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pydantic import BaseModel

TRANSFORMS = {
    "identity": lambda a: a, "flip_x": lambda a: a[:, ::-1], "flip_y": lambda a: a[::-1],
    "rot180": lambda a: a[::-1, ::-1], "transpose": lambda a: np.swapaxes(a, 0, 1),
    "rot90": lambda a: np.rot90(a, 1), "rot270": lambda a: np.rot90(a, 3),
    "anti_transpose": lambda a: np.rot90(np.swapaxes(a, 0, 1), 2),
}


class TransformScore(BaseModel):
    transform: str
    mean_abs_diff: float
    pearson_r: float


class OimComparison(BaseModel):
    kind: str
    oim_image: str
    crop_box_px: tuple[int, int, int, int]       # left, top, right, bottom in the OIM image
    best: TransformScore
    identity: TransformScore
    all: list[TransformScore]
    frame_ok: bool                               # identity is (one of) the best
    note: str = ""


def hex_raster(x: np.ndarray, y: np.ndarray, values: np.ndarray, fill=np.nan) -> np.ndarray:
    """Raster image (rows = y, columns = half hex columns) of per-pixel values (n,) or (n,c)."""
    ys = np.unique(np.round(y, 4))
    dy = float(np.median(np.diff(ys))) if ys.size > 1 else 1.0
    row = np.round((y - ys[0]) / dy).astype(int)
    xr = np.sort(x[row == 0])
    dx = float(np.median(np.diff(xr))) if xr.size > 1 else 1.0
    h = np.round((x - x.min()) / (dx / 2)).astype(int)
    H, W = row.max() + 1, h.max() + 2
    v = values.reshape(len(x), -1)
    img = np.full((H, W, v.shape[1]), fill, float)
    img[row, h] = v
    img[row, h + 1] = v
    return img[..., 0] if values.ndim == 1 else img


def find_map_box(img: np.ndarray, tol: float = 0.04) -> tuple[int, int, int, int]:
    """Bounding box of the map area: rows/columns that are not uniform background (white/black
    margins, typical of OIM exports). Legends outside the largest block are cut off by taking the
    longest run of 'busy' rows and columns."""
    g = img.mean(axis=2) if img.ndim == 3 else img
    busy_r = g.std(axis=1) > tol
    busy_c = g.std(axis=0) > tol

    def longest(m):
        best, start, cur = (0, 0), None, 0
        for i, b in enumerate(np.append(m, False)):
            if b and start is None:
                start = i
            if not b and start is not None:
                if i - start > best[1] - best[0]:
                    best = (start, i)
                start = None
        return best
    r0, r1 = longest(busy_r)
    c0, c1 = longest(busy_c)
    return c0, r0, c1, r1


def _resize(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    from PIL import Image  # noqa: PLC0415

    if a.ndim == 2:
        return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize(shape[::-1], Image.BILINEAR))
    return np.stack([_resize(a[..., k], shape) for k in range(a.shape[2])], axis=2)


def _score(ours: np.ndarray, ref: np.ndarray, kind: str) -> tuple[float, float]:
    ok = np.isfinite(ours).all(axis=-1) if ours.ndim == 3 else np.isfinite(ours)
    if kind == "ipf":
        d = float(np.abs(ours[ok] - ref[ok]).mean())
        rs = [np.corrcoef(ours[..., k][ok], ref[..., k][ok])[0, 1] for k in range(3)]
        return d, float(np.mean(rs))
    a, b = ours[ok], (ref.mean(axis=2) if ref.ndim == 3 else ref)[ok]
    a = (a - a.mean()) / (a.std() + 1e-12)
    b = (b - b.mean()) / (b.std() + 1e-12)
    r = float(np.mean(a * b))
    return float(np.abs(a - b).mean()), r


def compare(x: np.ndarray, y: np.ndarray, values: np.ndarray, oim_image: Path, kind: str,
            out_png: Path | None = None) -> OimComparison:
    """values: (n,3) IPF RGB in 0..1 (same key + direction as the OIM image) or (n,) CI."""
    from PIL import Image  # noqa: PLC0415

    ref_full = np.asarray(Image.open(oim_image).convert("RGB"), float) / 255.0
    box = find_map_box(ref_full)
    ref = ref_full[box[1]:box[3], box[0]:box[2]]
    ours_raster = hex_raster(x, y, values)
    scores = []
    for name, t in TRANSFORMS.items():
        o = t(ours_raster)
        o = _resize(np.nan_to_num(o, nan=1.0 if kind == "ipf" else 0.0), ref.shape[:2])
        d, r = _score(o, ref if kind == "ipf" else ref, kind)
        scores.append(TransformScore(transform=name, mean_abs_diff=d, pearson_r=r))
    best = max(scores, key=lambda s: s.pearson_r)
    ident = next(s for s in scores if s.transform == "identity")
    res = OimComparison(kind=kind, oim_image=str(oim_image), crop_box_px=box, best=best, identity=ident,
                        all=scores, frame_ok=ident.pearson_r >= best.pearson_r - 0.02,
                        note="frame looks consistent" if ident.pearson_r >= best.pearson_r - 0.02
                        else f"best match needs '{best.transform}': check the reference frame / EDAX setting")
    if out_png is not None:
        _side_by_side(ours_raster, ref, res, kind, out_png)
    return res


def _side_by_side(ours, ref, res: OimComparison, kind: str, out_png: Path) -> None:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    fig, ax = plt.subplots(1, 2, figsize=(10, 5))
    ax[0].imshow(np.clip(np.nan_to_num(ours, nan=1.0), 0, 1) if kind == "ipf" else ours, cmap=None if kind == "ipf" else "gray",
                 aspect=0.866 if kind == "ipf" else 0.866)
    ax[0].set_title("this app")
    ax[1].imshow(ref)
    ax[1].set_title("OIM (map area)")
    for a in ax:
        a.axis("off")
    fig.suptitle(f"{kind}: identity r = {res.identity.pearson_r:.3f}, best '{res.best.transform}' "
                 f"r = {res.best.pearson_r:.3f} — {res.note}", fontsize=9)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
