"""modules/grain_size.py on synthetic partitions with known answers (square and hex grids)."""
import numpy as np
import pytest

from ti64app.modules.grain_size import (GridIndex, chords, directional_intercepts, dist_stats, hist_edges,
                                        lath_intercepts, lognormal_pdf, moment_ellipse, read_oim_grain_file)


def grid(nx, ny, step=0.2, kind="hex"):
    """Pixel centres in OIM file order; hex: rows dy = step sqrt3/2, odd rows offset by step/2."""
    if kind == "square":
        yy, xx = np.mgrid[0:ny, 0:nx]
        return xx.ravel() * step, yy.ravel() * step
    dy = step * np.sqrt(3) / 2
    xs, ys = [], []
    for r in range(ny):
        n = nx if r % 2 == 0 else nx - 1
        xs.append(np.arange(n) * step + (step / 2 if r % 2 else 0))
        ys.append(np.full(n, r * dy))
    return np.concatenate(xs), np.concatenate(ys)


def rot(x, y, cx, cy, deg):
    t = np.radians(deg)
    dx, dy = x - cx, y - cy
    return dx * np.cos(t) + dy * np.sin(t), -dx * np.sin(t) + dy * np.cos(t)


@pytest.mark.parametrize("kind", ["square", "hex"])
@pytest.mark.parametrize("angle", [0.0, 30.0, 117.0])
def test_rectangle_lath_thickness_and_ellipse(kind, angle):
    step = 0.1
    x, y = grid(400, 400 if kind == "square" else 460, step, kind)
    cx, cy = 20.0, 20.0
    u, v = rot(x, y, cx, cy, angle)
    w, L = 1.2, 12.0                                       # lath 1.2 x 12 um
    lab = np.where((np.abs(u) <= L / 2) & (np.abs(v) <= w / 2), 1, 2)
    e = moment_ellipse(x, y, lab, np.array([1]), step, kind)
    assert e["angle_deg"][0] == pytest.approx(angle % 180, abs=1.0)
    # second-moment ellipse of a w x L rectangle: minor 2w/sqrt3, major 2L/sqrt3 (pixelation ~ step)
    assert e["minor_um"][0] == pytest.approx(2 * w / np.sqrt(3), abs=step)
    assert e["major_um"][0] == pytest.approx(2 * L / np.sqrt(3), rel=0.02)
    gi = GridIndex(x, y, step)
    li = lath_intercepts(gi, lab, np.array([1]), e["angle_deg"], step, step / 4)
    assert li["n_chords"][0] >= 0.8 * L / step
    # median chord = pixelated width (hex: rows dy = 0.087 um apart); mean is lowered by the jagged tips
    assert li["median_chord_um"][0] == pytest.approx(w, abs=step)
    assert li["mean_chord_um"][0] <= li["median_chord_um"][0] + 1e-9


def test_ellipse_moments_exact_for_ellipse():
    step = 0.1
    x, y = grid(500, 580, step, "hex")
    u, v = rot(x, y, 25, 25, 40)
    lab = np.where((u / 8) ** 2 + (v / 2) ** 2 <= 1, 1, 0)
    e = moment_ellipse(x, y, lab, np.array([1]), step, "hex")
    assert e["major_um"][0] == pytest.approx(16, rel=0.01) and e["minor_um"][0] == pytest.approx(4, rel=0.02)
    assert e["angle_deg"][0] == pytest.approx(40, abs=0.5)


def test_chords_truncation_and_e112():
    """Stripes of width 2 um across a 20 um map along x: chords along x through the stripes are complete
    inside and truncated at the map edges; E112 counts the truncated ones half."""
    step = 0.1
    x, y = grid(200, 100, step, "square")                  # 20 x 10 um
    lab = (np.floor(x / 2.0).astype(int) + 1)               # 10 stripes along x, each 2 um wide
    gi = GridIndex(x, y, step)
    c = directional_intercepts(gi, lab, 0.0, 1.0, step / 4)
    full = c["length_um"][~c["truncated"]]
    assert np.allclose(full, 2.0, atol=0.06)
    assert c["n_truncated"] == 2 * c["n_complete"] / 8      # first + last stripe on each line
    total = c["length_um"].sum()
    assert c["mean_e112_um"] == pytest.approx(total / (c["n_complete"] + 0.5 * c["n_truncated"]))
    # perpendicular direction: every chord is cut by the map edge on both sides
    c2 = directional_intercepts(gi, lab, 90.0, 0.5, step / 4)
    assert c2["truncated"].all() and c2["n_complete"] == 0


def test_unassigned_pixels_truncate_but_other_grains_do_not():
    step = 0.1
    x, y = grid(300, 50, step, "square")
    lab = np.full(x.size, 1)
    lab[(x > 10) & (x < 20)] = 2
    lab[(x > 20.5) & (x < 21)] = 0                         # unassigned gap
    gi = GridIndex(x, y, step)
    count = np.array([False, False, True])
    c = chords(gi, lab, 0.0, 1.0, step / 4, count)
    assert (c["label"] == 2).all()
    assert not c["truncated"].any() and np.allclose(c["length_um"], 9.9, atol=0.06)


def test_dist_stats_lognormal_recovery():
    rng = np.random.default_rng(1)
    v = rng.lognormal(0.5, 0.4, 20000)
    s = dist_stats(v, v ** 2)
    assert s["lognormal"]["mu"] == pytest.approx(0.5, abs=0.01)
    assert s["lognormal"]["sigma"] == pytest.approx(0.4, abs=0.01)
    assert s["lognormal"]["ks_p"] > 0.01
    # area-weighted (weights ~ d^2) of a log-normal is log-normal with mu + 2 sigma^2, same sigma
    aw = s["area_weighted"]
    assert aw["lognormal"]["mu"] == pytest.approx(0.5 + 2 * 0.16, abs=0.02)
    assert aw["lognormal"]["sigma"] == pytest.approx(0.4, abs=0.02)
    assert aw["mean"] == pytest.approx((v ** 3).sum() / (v ** 2).sum())
    e = hist_edges(v)
    assert e[0] == 0 and 5 <= len(e) - 1 <= 40
    pdf = lognormal_pdf(np.linspace(0.01, 20, 4000), 0.5, 0.4)
    assert np.trapezoid(pdf, dx=(20 - 0.01) / 3999) == pytest.approx(1, abs=1e-3)
    assert dist_stats(np.array([])) is None


def test_read_oim_grain_file(tmp_path):
    f = tmp_path / "grains.txt"
    f.write_text("# Header: synthetic\n"
                 "# Column 1: Integrated Grain ID\n# Column 2: Grain Size (# of measurement points)\n"
                 "# Column 3: Grain Size (area in square microns)\n# Column 4: Grain Size (diameter in microns)\n"
                 "# Column 5: Edge grain (1) or not (0)\n# Column 6: Phase\n#\n"
                 "1 10 0.35 0.667 0 1\n2 100 3.46 2.10 1 1\n")
    d = read_oim_grain_file(f)
    assert d["id"].tolist() == [1, 2] and d["n_px"].tolist() == [10, 100]
    assert d["ecd_um"].tolist() == [0.667, 2.10] and d["is_edge"].tolist() == [0, 1]
    assert "column 4" in d["matched"]["ecd_um"]
