"""Python-only reconstruction analysis: Ti variant / packet numbering and grain shape (synthetic data)."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from ti64app.modules.recon_analysis import (BCC110, BCC111_IN_110, canonical_cubic, grain_mean_parent, grain_shape,
                                            quat_to_matrix, ti_variants, variant_stats)


def _mtex_quat(R: np.ndarray) -> np.ndarray:
    """rotation matrices (n,3,3) -> quaternions a b c d (scalar first)."""
    x, y, z, w = Rotation.from_matrix(R).as_quat().T
    return np.stack([w, x, y, z], 1)


def _burgers_children(parent_R: np.ndarray):
    """All 12 ideal Burgers alpha orientations of one beta orientation, in our numbering:
    c || {110} normal k, a1 || <111> j in that plane."""
    out = []
    for k, n in enumerate(BCC110):
        for j, d in enumerate(BCC111_IN_110[k]):
            Rc = np.stack([d, np.cross(n, d), n], 1)          # columns: a (X), b* (Y = Z x X), c (Z)
            out.append((k + 1, 2 * k + j + 1, parent_R @ Rc))
    return out


def test_quat_to_matrix_matches_scipy():
    R = Rotation.random(20, random_state=1).as_matrix()
    np.testing.assert_allclose(quat_to_matrix(_mtex_quat(R)), R, atol=1e-12)


def test_tables():
    assert BCC111_IN_110.shape == (6, 2, 3)
    assert np.allclose(np.einsum("kjx,kx->kj", BCC111_IN_110, BCC110), 0)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_ideal_burgers_variants_numbered(seed):
    Rp = Rotation.random(random_state=seed).as_matrix()
    kids = _burgers_children(Rp)
    cq = _mtex_quat(np.stack([R for *_, R in kids]))
    pq = np.repeat(_mtex_quat(Rp[None]), len(kids), 0)
    v = ti_variants(cq, pq)
    assert v["packet"].tolist() == [k for k, _, _ in kids]
    assert v["variant"].tolist() == list(range(1, 13))
    assert np.nanmax(v["c_dev_deg"]) < 1e-5 and np.nanmax(v["a_dev_deg"]) < 1e-5


def test_numbering_invariant_to_hex_symmetry_and_specimen_rotation():
    Rp = Rotation.random(random_state=5).as_matrix()
    kids = _burgers_children(Rp)
    hex6 = Rotation.from_euler("z", 60, degrees=True).as_matrix()      # 6-fold about c (crystal symmetry)
    flip = Rotation.from_euler("x", 180, degrees=True).as_matrix()      # 2-fold about a
    cq = _mtex_quat(np.stack([R @ hex6 @ flip for *_, R in kids]))
    pq = np.repeat(_mtex_quat(Rp[None]), 12, 0)
    assert ti_variants(cq, pq)["variant"].tolist() == list(range(1, 13))
    S = Rotation.random(random_state=9).as_matrix()                     # rotate the specimen: same IDs
    cq2 = _mtex_quat(np.stack([S @ R for *_, R in kids]))
    pq2 = np.repeat(_mtex_quat((S @ Rp)[None]), 12, 0)
    assert ti_variants(cq2, pq2)["variant"].tolist() == list(range(1, 13))


def test_deviation_reported_and_nan_rows():
    Rp = np.eye(3)
    _, _, Rc = _burgers_children(Rp)[3]
    tilt = Rotation.from_euler("y", 2.0, degrees=True).as_matrix()
    cq = np.vstack([_mtex_quat((tilt @ Rc)[None]), np.full((1, 4), np.nan)])
    pq = np.vstack([_mtex_quat(Rp[None]), _mtex_quat(Rp[None])])
    v = ti_variants(cq, pq)
    assert v["variant"].tolist() == [4, 0]
    assert 0.5 < v["c_dev_deg"][0] <= 2.0 + 1e-6 and np.isnan(v["c_dev_deg"][1])


def test_variant_stats_and_grain_mean():
    pg = np.array([1, 1, 1, 1, 2, 2, 0])
    var = np.array([1, 1, 2, 3, 5, 5, 0])
    pk = (var + 1) // 2
    fit = np.array([1.0, 2.0, 3.0, 4.0, 1.0, 1.0, np.nan])
    s = variant_stats(pg, var, pk, fit, min_share=0.2)
    g = {d["grain"]: d for d in s["grains"]}
    assert g[1]["n_variants"] == 3 and g[1]["n_packets"] == 2 and g[1]["largest_variant_share"] == 0.5
    assert g[2]["n_variants"] == 1
    assert s["n_variants_hist"][1] == 1 and s["n_variants_hist"][3] == 1
    assert s["variant_mean_fit_deg"][0] == pytest.approx(1.5)
    q2 = _mtex_quat(Rotation.from_euler("z", 10, degrees=True).as_matrix()[None])[0]
    grain = {"id": np.array([2, 1]), "mean_quat": np.array([q2, [0, 1.0, 0, 0]])}   # 180 deg about x ~ identity
    m = grain_mean_parent(pg, grain)
    assert np.allclose(m[0], [1, 0, 0, 0]) and np.allclose(m[4], q2) and np.isnan(m[6]).all()


def test_canonical_cubic_is_the_same_for_all_equivalents():
    Rp = Rotation.random(random_state=3).as_matrix()
    sym = Rotation.create_group("O").as_matrix()
    eq = _mtex_quat(np.stack([Rp @ s for s in sym]))                      # 24 equivalents of one orientation
    eq[::2] *= -1                                                          # and both quaternion signs
    c = canonical_cubic(eq)
    assert np.allclose(c, c[0], atol=1e-9) and c[0, 0] >= 0
    Rc = quat_to_matrix(c[:1])[0]
    d = Rp.T @ Rc                                                          # must be a cubic symmetry operation
    assert min(np.abs(d - s).max() for s in sym) < 1e-9


def test_grain_shape_caliper_along_build_direction():
    step = 0.5
    xs, ys = np.meshgrid(np.arange(0, 20, step), np.arange(0, 5, step))   # 20 um along x, 5 um along y
    x, y = xs.ravel(), ys.ravel()
    pg = np.ones(x.size, int)
    grain = {"id": np.array([1]), "n_px": np.array([x.size]), "area_um2": np.array([100.0]),
             "ecd_um": np.array([2 * np.sqrt(100 / np.pi)]), "is_edge": np.array([False])}
    r = grain_shape(x, y, pg, grain, (1.0, 0.0, 0.0), step, min_px=1, min_ecd_um=0)
    assert r["table"]["along_bd_um"][0] == pytest.approx(20.0)
    assert r["table"]["across_bd_um"][0] == pytest.approx(5.0)
    assert r["summary"]["aspect_along_across"]["mean"] == pytest.approx(4.0)
    r90 = grain_shape(x, y, pg, grain, (0.0, -1.0, 0.0), step, 1, 0)    # BD along -y: swapped
    assert r90["table"]["aspect"][0] == pytest.approx(0.25)
    out = grain_shape(x, y, pg, grain, (0.0, 0.0, 1.0), step, 1, 0)     # BD normal to the map
    assert out["summary"]["along_bd_um"] is None and out["summary"]["warnings"]
    grain["is_edge"] = np.array([True])                                  # edge grains are excluded
    assert grain_shape(x, y, pg, grain, (1.0, 0.0, 0.0), step, 1, 0)["summary"]["n_used"] == 0
