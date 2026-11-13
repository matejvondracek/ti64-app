"""Phase 6.6 fix 5, items 1, 3, 4 and 14 on the real maps (slow; both backends, MATLAB when available).

Probe point of JU04_area4 (row 86, column 305, 0-based, hex grid), checked against the OIM Analysis status bar
(data/reference/JU04/JU04_area4_OIM_IPF.jpg): Euler (147.4, 34.1, 98.4) deg, x,y 61.00,14.90 um, IQ 78013.4,
CI 0.436, crystal direction [5 -2 -3 7] || A3. docs/orientation_check.md.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ti64app import frame
from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams

REPO = Path(__file__).resolve().parents[2]
ANG4 = REPO / "data" / "JU04" / "JU04_area4.ang"
I_PROBE = 43 * 1501 + 43 * 1500 + 305           # rows alternate NCOLS_ODD 1501 / NCOLS_EVEN 1500
EULER_FILE = (2.57265, 0.59602, 1.71747)

pytestmark = pytest.mark.slow


def _backend(name):
    be = get_backend(name)
    if not be.is_available():
        pytest.skip(f"{name} backend unavailable: {be.unavailable_reason()}")
    return be


@pytest.fixture(scope="module", params=["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
def area4(request):
    if not ANG4.is_file():
        pytest.skip("data/JU04/JU04_area4.ang missing (git-ignored)")
    be = _backend(request.param)
    h = be.load_ebsd(LoadParams(file=ANG4, input_state="raw", setting=2, phase_names={1: "Ti (alpha)"}))
    d = be.get_ebsd(h)
    yield be, h, d
    be.release(h)


def _rot(q, v):
    a, u = q[0], np.asarray(q[1:], float)
    v = np.asarray(v, float)
    return v + 2 * np.cross(u, np.cross(u, v) + a * v)


def _hex_invariants(v):
    """Direction in the hexagonal crystal frame (X||a): angle to c and in-plane azimuth folded to 0..30 deg (6/mmm)."""
    v = v / np.linalg.norm(v)
    ph = np.degrees(np.arctan2(v[1], v[0])) % 60
    return np.degrees(np.arccos(abs(v[2]))), min(ph, 60 - ph)


def test_probe_point_matches_oim(area4):
    be, h, d = area4
    i = I_PROBE
    assert (d.x[i], round(float(d.y[i]), 4)) == (61.0, 14.8956)
    assert d.props["iq"][i] == pytest.approx(78013.4) and d.props["ci"][i] == pytest.approx(0.436)
    e = frame.file_euler(ANG4)
    assert np.allclose(e[i], EULER_FILE)
    assert np.allclose(np.degrees(e[i]), (147.4, 34.1, 98.4), atol=0.06)            # OIM status bar
    # the backend holds the file orientation rotated by the setting-2 rotation (180 deg about (1,-1,0))
    assert frame.rotation_angle_deg(d.quat[i], frame.app_quat_from_file_euler(e[i], 2)) < 1e-3
    assert np.allclose(np.degrees(frame.quat2euler(d.quat[i])), (302.598, 145.851, 278.404), atol=0.01)


def test_crystal_frame_x_parallel_a(area4):
    """Item 3: the .ang says 'a || x, c* || z'; both backends load X||a. The crystal direction || A3 of the probe
    matches OIM's [5 -2 -3 7] with X||a; X||a* (30 deg about c) does not."""
    be, h, d = area4
    from ti64app.io.ang import read_header

    assert frame.check_alignment(read_header(ANG4).fields.get("CartesianAlignment")) is None
    assert any("X||a" in v and "X||a*" not in v for v in d.alignment.values()), d.alignment
    a, c = 2.943, 4.698
    U, V, T, W = 5, -2, -3, 7
    oim = (U - T) * np.array([a, 0, 0]) + (V - T) * np.array([-a / 2, a * np.sqrt(3) / 2, 0]) + W * np.array([0, 0, c])
    q = d.quat[I_PROBE] * np.array([1, -1, -1, -1])                    # inverse: specimen -> crystal
    ours = _rot(q, [0, 0, -1])                                         # A3 = -z_app
    t_o, p_o = _hex_invariants(oim)
    t_a, p_a = _hex_invariants(ours)
    assert abs(t_a - t_o) < 1.0 and abs(p_a - p_o) < 3.0               # OIM rounds to integer indices
    q30 = frame.qmul(d.quat[I_PROBE], [np.cos(np.radians(15)), 0, 0, np.sin(np.radians(15))])
    _t, p30 = _hex_invariants(_rot(q30 * np.array([1, -1, -1, -1]), [0, 0, -1]))
    assert abs(p30 - p_o) > 10.0


@pytest.mark.parametrize("app_axis,oim_axis", [((1, 0, 0), (0, 1, 0)), ((0, 1, 0), (1, 0, 0)), ((0, 0, 1), (0, 0, 1))])
def test_ipf_colour_of_probe_equals_oim_axis(area4, app_axis, oim_axis):
    """Item 4: IPF-X of the app = OIM IPF along A2, IPF-Y = along A1, IPF-Z = along A3 (TSL key, probe point)."""
    be, h, d = area4
    got = be.ipf_colors(h, "Ti (alpha)", "ipfTSLKey", app_axis)[I_PROBE]
    q_file = frame.euler2quat(*EULER_FILE)[None, :]                     # the orientation as OIM has it (A1 A2 A3)
    want = be.ipf_colors_quat(h, "Ti (alpha)", q_file, "ipfTSLKey", oim_axis)[0]
    # 5-decimal Euler angles of the file vs the stored rotation: colours agree to ~0.005; a wrong axis is far off
    assert np.allclose(got, want, atol=0.01), (got, want)
    wrong = {(1, 0, 0): (0, 1, 0), (0, 1, 0): (1, 0, 0), (0, 0, 1): (1, 0, 0)}[tuple(oim_axis)]
    other = be.ipf_colors_quat(h, "Ti (alpha)", q_file, "ipfTSLKey", wrong)[0]
    assert np.abs(got - other).max() > 0.05


# ---- item 14: every .ang in data/ draws IPF, phase, KAM and grains in both backends ---------------------------------
ANGS = sorted(f for f in (REPO / "data").rglob("*.ang") if "crops" not in f.parts)


def _head_crop(src: Path, dst: Path, n_rows: int = 120) -> Path:
    """The header and the first n_rows scan rows of an .ang (NROWS adjusted): same phases and names, small."""
    head, rows = [], []
    with open(src, encoding="latin-1") as fh:
        for line in fh:
            if line.startswith("#"):
                head.append(line)
                continue
            rows.append(line)
            break
        hexg = any("HexGrid" in h_ for h_ in head)
        no = int(next(h_ for h_ in head if "NCOLS_ODD" in h_).split(":")[1])
        ne = int(next(h_ for h_ in head if "NCOLS_EVEN" in h_).split(":")[1])
        n = sum((no if (r % 2 == 0 or not hexg) else ne) for r in range(n_rows))
        for line in fh:
            if len(rows) >= n:
                break
            rows.append(line)
    head = [f"# NROWS: {n_rows}\n" if h_.startswith("# NROWS") else h_ for h_ in head]
    dst.write_text("".join(head + rows), encoding="latin-1")
    return dst


def _draw_all(project, map_id):
    from ti64app import service

    service.run(project, "ebsd.load", {"map_id": map_id})
    for k in ("ipf", "phase"):
        service.run(project, "figure", {"map_id": map_id, "kind": k})
    service.run(project, "grains.calc", {"map_id": map_id})
    service.run(project, "map.compute", {"map_id": map_id, "kind": "kam"})
    service.run(project, "figure", {"map_id": map_id, "kind": "kam", "overlays": ["hagb"]})


@pytest.mark.parametrize("backend", ["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
@pytest.mark.parametrize("ang", ANGS, ids=[f.stem[:40] for f in ANGS])
def test_every_ang_draws(ang, backend, tmp_path):
    """APEX ('Ti (Titanium, alpha)') and OIM ('Ti (alpha)') phase names, single- and two-phase files: IPF, phase,
    grains and KAM all draw (head crop of 120 rows of each file, so every header is covered quickly)."""
    _backend(backend)
    from ti64app import service
    from ti64app.project import Project

    crop = _head_crop(ang, tmp_path / f"{ang.stem[:30]}_head.ang")
    p = Project.create("all_ang", base=tmp_path, backend=backend)
    service.run(p, "sample.set", {"map_id": "M", "map_file": str(crop), "input_state": "raw"})
    _draw_all(p, "M")
