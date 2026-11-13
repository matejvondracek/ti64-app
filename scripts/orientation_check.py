"""Orientation check of JU04_area4 against OIM Analysis (phase 6.6 fix 5, items 2 + 4; docs/orientation_check.md).

1. Loads data/JU04/JU04_area4.ang with the given backend (EDAX setting 2, as the app does).
2. Colours the alpha phase with ipfTSLKey along app Z (OIM: 'Sample Direction [0 0 1]' = A3; app z = -A3, same
   colours: the IPF key uses the Laue group, which contains the inversion) and rasters it in map coordinates
   (x right, y down, first row on top = OIM display, origin top left).
3. Rectifies the map area of the OIM photo (monitor photo -> perspective): 4 corners clicked once (CORNERS), refined
   by ECC (homography) against the app render.
4. Scores the 8 flips / rotations of the app render and the other load settings (0, 1, 3, 4) and a 30 deg rotation
   about c (X||a vs X||a*), so a frame error shows up as a better score than the identity.
5. Writes OIM | app | |difference| side by side and a JSON with the numbers to projects/orientation_check/.

Usage:  python scripts/orientation_check.py [pymtex|matlab]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

ANG = ROOT / "data" / "JU04" / "JU04_area4.ang"
PHOTO = ROOT / "data" / "reference" / "JU04" / "JU04_area4_OIM_IPF.jpg"
# map corners in the photo (px): top left, top right, bottom right, bottom left (read off the photo, refined by ECC)
CORNERS = np.float32([[455, 291], [876, 296], [874, 716], [451, 714]])
OUT = ROOT / "projects" / "orientation_check"
SIZE = 420                         # rectified size (px); the map is 300.2 x 300.0 um
SCALEBAR = (0.0, 0.88, 0.37, 1.0)  # OIM scale-bar box (x0, y0, x1, y1 as fractions): ignored
PROBE = dict(row=86, col=305, x=61.0, y=14.89564, euler_file_deg=(147.4, 34.1, 98.4), iq=78013.4, ci=0.436)

TRANSFORMS = {
    "identity": lambda a: a, "mirror_x": lambda a: a[:, ::-1], "mirror_y": lambda a: a[::-1],
    "rot180": lambda a: a[::-1, ::-1], "transpose": lambda a: np.swapaxes(a, 0, 1),
    "rot90": lambda a: np.rot90(a, 1), "rot270": lambda a: np.rot90(a, 3),
    "anti_transpose": lambda a: np.rot90(np.swapaxes(a, 0, 1), 2),
}


def raster(x, y, rgb, size=SIZE):
    """Nearest-point raster of the map (rows = y down, first row on top) at size x size px."""
    xs = (x - x.min()) / (x.max() - x.min()) * (size - 1)
    ys = (y - y.min()) / (y.max() - y.min()) * (size - 1)
    img = np.ones((size, size, 3))
    acc = np.zeros((size, size, 3))
    cnt = np.zeros((size, size))
    ok = np.isfinite(rgb[:, 0])
    r, c = np.round(ys[ok]).astype(int), np.round(xs[ok]).astype(int)
    np.add.at(acc, (r, c), rgb[ok])
    np.add.at(cnt, (r, c), 1)
    m = cnt > 0
    img[m] = acc[m] / cnt[m][:, None]
    return img, m


def rectify(photo_rgb, app_img):
    import cv2  # noqa: PLC0415

    dst = np.float32([[0, 0], [SIZE - 1, 0], [SIZE - 1, SIZE - 1], [0, SIZE - 1]])
    H = cv2.getPerspectiveTransform(CORNERS, dst).astype(np.float32)
    warp = cv2.warpPerspective(photo_rgb, H, (SIZE, SIZE), flags=cv2.INTER_AREA)
    # ECC refinement on a grey image (the photo's colours are distorted, the pattern is not)
    a = cv2.cvtColor((app_img * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = cv2.cvtColor(warp, cv2.COLOR_RGB2GRAY).astype(np.float32)
    W = np.eye(3, dtype=np.float32)
    try:
        _cc, W = cv2.findTransformECC(a, b, W, cv2.MOTION_HOMOGRAPHY,
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
        warp = cv2.warpPerspective(warp, W, (SIZE, SIZE), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    except cv2.error:
        pass
    return warp.astype(float) / 255.0, W


def colour_fit(src, dst, mask):
    """Least-squares affine colour map src -> dst (photo of a monitor: white balance, contrast, gamma-ish)."""
    A = np.c_[src[mask], np.ones(mask.sum())]
    M, *_ = np.linalg.lstsq(A, dst[mask], rcond=None)
    out = np.c_[src.reshape(-1, 3), np.ones(src.shape[0] * src.shape[1])] @ M
    return np.clip(out.reshape(src.shape), 0, 1)


def score(app, oim, mask):
    """Median RGB distance (0..sqrt 3) after fitting the OIM photo's colours to the app colours, and the mean
    Pearson r of the three channels (pattern only)."""
    fit = colour_fit(oim, app, mask)
    d = np.linalg.norm(fit - app, axis=2)
    r = np.mean([np.corrcoef(app[..., k][mask], oim[..., k][mask])[0, 1] for k in range(3)])
    return float(np.median(d[mask])), float(r), fit, d


def app_colours(backend, setting=2, rot_c30=False):
    from ti64app.backends import get_backend  # noqa: PLC0415
    from ti64app.backends.base import LoadParams  # noqa: PLC0415

    be = get_backend(backend)
    h = be.load_ebsd(LoadParams(file=ANG, input_state="raw", setting=setting, phase_names={1: "Ti (alpha)"}))
    d = be.get_ebsd(h)
    if not rot_c30:
        rgb = be.ipf_colors(h, "Ti (alpha)", "ipfTSLKey", (0.0, 0.0, 1.0))
    else:                                  # crystal frame rotated 30 deg about c (X||a* instead of X||a)
        q = d.quat.copy()
        r = np.array([np.cos(np.radians(15)), 0, 0, np.sin(np.radians(15))])     # 30 deg about z (crystal side)
        a1, b1, c1, d1 = q.T
        a2, b2, c2, d2 = r
        q = np.stack([a1 * a2 - b1 * b2 - c1 * c2 - d1 * d2, a1 * b2 + b1 * a2 + c1 * d2 - d1 * c2,
                      a1 * c2 - b1 * d2 + c1 * a2 + d1 * b2, a1 * d2 + b1 * c2 - c1 * b2 + d1 * a2], 1)
        rgb = np.full((d.n, 3), np.nan)
        m = d.phase == 1
        rgb[m] = be.ipf_colors_quat(h, "Ti (alpha)", q[m], "ipfTSLKey", (0.0, 0.0, 1.0))
    be.release(h)
    return d, rgb


def main(backend="pymtex"):
    import cv2  # noqa: PLC0415

    OUT.mkdir(parents=True, exist_ok=True)
    d, rgb = app_colours(backend)
    app, filled = raster(d.x, d.y, rgb)
    photo = cv2.cvtColor(cv2.imread(str(PHOTO)), cv2.COLOR_BGR2RGB)
    oim, W = rectify(photo, app)
    mask = filled.copy()
    x0, y0, x1, y1 = (int(v * SIZE) for v in SCALEBAR)
    mask[y0:y1, x0:x1] = False
    mask[:4], mask[-4:], mask[:, :4], mask[:, -4:] = False, False, False, False

    res = {"backend": backend, "photo": str(PHOTO.relative_to(ROOT)), "corners_px": CORNERS.tolist(),
           "ecc_homography": np.asarray(W).tolist(), "transforms": {}, "settings": {}}
    for name, t in TRANSFORMS.items():
        med, r, *_ = score(t(app), oim, mask & t(mask))
        res["transforms"][name] = {"median_rgb_diff": round(med, 4), "pearson_r": round(r, 4)}
    for s in (0, 1, 3, 4):
        _d, c = app_colours(backend, setting=s)
        a, _ = raster(_d.x, _d.y, c)
        med, r, *_ = score(a, oim, mask)
        res["settings"][f"setting {s}"] = {"median_rgb_diff": round(med, 4), "pearson_r": round(r, 4)}
    _d, c = app_colours(backend, rot_c30=True)
    a, _ = raster(_d.x, _d.y, c)
    med, r, *_ = score(a, oim, mask)
    res["settings"]["setting 2, crystal rotated 30 deg about c (X||a*)"] = {"median_rgb_diff": round(med, 4),
                                                                          "pearson_r": round(r, 4)}
    med, r, fit, diff = score(app, oim, mask)
    res["identity_setting2"] = {"median_rgb_diff": round(med, 4), "pearson_r": round(r, 4),
                                "p90_rgb_diff": round(float(np.percentile(diff[mask], 90)), 4)}
    raw = np.linalg.norm(oim - app, axis=2)
    res["identity_setting2"]["median_rgb_diff_uncorrected"] = round(float(np.median(raw[mask])), 4)
    # decision by the pattern correlation: the photo of the monitor is washed out (saturation 0.10-0.15 everywhere),
    # so the colour distance is dominated by the photo, not by the orientations. IPF along z cannot tell the load
    # settings apart (each is a 180 deg rotation about z or an in-plane axis: z -> +-z, same colours); they are
    # checked with the probe point (Euler angles, crystal direction || A3) in tests/integration/test_fix5_orientation.py
    best = max(res["transforms"], key=lambda k: res["transforms"][k]["pearson_r"])
    res["best_transform"] = best
    c30 = res["settings"]["setting 2, crystal rotated 30 deg about c (X||a*)"]["pearson_r"]
    ok = best == "identity" and c30 < res["transforms"]["identity"]["pearson_r"] - 0.1
    res["verdict"] = ("match: not mirrored or rotated, crystal frame X||a" if ok else "MISMATCH")
    # side by side: OIM (rectified, colour-corrected) | app | |difference|
    dimg = np.clip(diff / 0.5, 0, 1)
    dimg[~mask] = 0
    panel = np.concatenate([oim, np.ones((SIZE, 8, 3)), fit, np.ones((SIZE, 8, 3)), app, np.ones((SIZE, 8, 3)),
                            np.repeat(dimg[..., None], 3, 2)], axis=1)
    out_png = OUT / f"JU04_area4_oim_vs_app_{backend}.png"
    cv2.imwrite(str(out_png), cv2.cvtColor((panel * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    res["figure"] = str(out_png.relative_to(ROOT))
    res["figure_panels"] = "OIM photo rectified | OIM colour-corrected | app (ipfTSLKey, Z) | |difference| (white = 0.5)"
    (OUT / f"result_{backend}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pymtex")
