"""Phase 6.7 (pymtex): texture (ODF, PF / IPF, specimen frame) and Young's modulus from texture.

Item 12: random orientations -> isotropic E equal to the Voigt / Reuss / Hill averages of the single crystal; a single
orientation -> the single-crystal directional modulus; Reuss <= Hill <= Voigt in every direction. Conformance of the
two backends: tests/conformance/test_phase67.py."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tests.conftest import sample_params
from ti64app import elastic as el
from ti64app import service
from ti64app import texture as tx

MID = "JU02_c300"


def _random_quats(n: int, seed: int = 0) -> np.ndarray:
    """Uniformly distributed rotations (Shoemake)."""
    u = np.random.default_rng(seed).random((n, 3))
    a, b = np.sqrt(1 - u[:, 0]), np.sqrt(u[:, 0])
    return np.stack([b * np.cos(2 * np.pi * u[:, 2]), a * np.sin(2 * np.pi * u[:, 1]),
                     a * np.cos(2 * np.pi * u[:, 1]), b * np.sin(2 * np.pi * u[:, 2])], 1)


@pytest.fixture
def proj(project, crop300_ju02):
    service.run(project, "sample.set", {**sample_params(crop300_ju02),
                                        "position": {"map_x_axis": "+X", "map_y_axis": "-Y"}})
    service.run(project, "ebsd.load", {"map_id": MID})
    service.run(project, "grains.calc", {"map_id": MID})
    return project


@pytest.fixture
def handle(proj):
    from ti64app.backends import get_backend
    from ti64app.service import materialize

    load = service.latest_step(proj, ("ebsd.load",), map_id=MID)
    return get_backend("pymtex"), materialize(proj, load.id, "pymtex")


# ---- item 12: elastic averages -------------------------------------------------------------------------------------
@pytest.mark.parametrize("preset", [el.DEFAULT_ALPHA, el.DEFAULT_BETA])
def test_random_orientations_give_isotropic_averages(handle, preset):
    be, h = handle
    p = el.DEFAULT_PRESETS[preset]
    phase = "Ti (alpha)" if p.phase == "alpha" else "Ti (beta)"
    q = _random_quats(40_000)
    C, S = be.stiffness_rotate(h, phase, q, p.matrix(), np.ones(len(q)))
    av = el.averages(C, S)
    iso = el.isotropic_moduli(p.matrix())
    dirs = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1], [1, -2, 0.5]], float)
    for m in ("voigt", "reuss", "hill"):
        E = el.youngs_modulus(av[m], dirs)
        assert np.allclose(E, iso[m], rtol=4e-3), (m, E, iso[m])


def test_single_orientation_gives_single_crystal_modulus(handle):
    be, h = handle
    p = el.DEFAULT_PRESETS[el.DEFAULT_ALPHA]
    q = np.array([[0.9, 0.2, -0.3, 0.25]])
    q /= np.linalg.norm(q)
    C, S = be.stiffness_rotate(h, "Ti (alpha)", q, p.matrix(), np.ones(1))
    av = el.averages(C, S)
    g = el.rotation_matrix(q[0])
    n = np.array([[1, 0, 0], [0, 0.6, 0.8], [0.3, -0.4, 0.866]], float)
    want = el.youngs_modulus(p.matrix(), (g.T @ (n / np.linalg.norm(n, axis=1, keepdims=True)).T).T)
    for m in ("voigt", "reuss", "hill"):
        assert np.allclose(el.youngs_modulus(av[m], n), want, rtol=1e-9)
    assert el.youngs_modulus(p.matrix(), [[0, 0, 1]])[0] == pytest.approx(143.27, abs=0.01)   # alpha-Ti E along c


def test_reuss_hill_voigt_order_and_elastic_step(proj):
    s = service.run(proj, "elastic.run", {"map_id": MID, "bootstrap_runs": 30})
    summ = json.loads((proj.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    M = {k: np.array(v) for k, v in summ["matrices_GPa"].items()}
    d, _ = el.direction_grid(31, 121)
    Ev, Er, Eh = (el.youngs_modulus(M[k], d.reshape(-1, 3)) for k in ("voigt", "reuss", "hill"))
    assert np.all(Er <= Eh + 1e-9) and np.all(Eh <= Ev + 1e-9)
    rows = {r["direction"]: r for r in summ["E_table"]}
    assert set(rows) >= {"beam axis", "width", "thickness"} or set(rows) >= {"beam axis", "width"}
    assert all(r["sd_hill"] > 0 for r in rows.values())
    assert 100 < rows["beam axis"]["E_hill"] < 130
    assert summ["phases"]["alpha"]["doi"] == "10.1103/PhysRev.135.A482"
    assert any("porosity is ignored" in n for n in summ["notes"])
    assert summ["anisotropy"]["hill"]["ratio"] >= 1.0
    tab = __import__("ti64app.ops_texture", fromlist=["sample_E_table"]).sample_E_table(proj, "EBMX-1")
    assert tab[0]["map"] == MID and tab[0]["E_hill"] == rows["beam axis"]["E_hill"]


def test_presets_in_settings():
    from ti64app.settings import load_settings

    es = load_settings().elastic
    assert es.default_alpha.startswith("alpha: Fisher") and es.default_beta.startswith("beta: Ti15Mo")
    assert len([p for p in es.presets.values() if p.phase == "alpha"]) == 3
    with pytest.raises(ValueError):
        es.preset(es.default_beta, "alpha")
    p = es.presets[el.DEFAULT_ALPHA]
    assert p.C12 == pytest.approx(p.C11 - 2 * p.C66, abs=0.1)


# ---- texture -------------------------------------------------------------------------------------------------------
def test_random_odf_is_near_uniform(handle):
    be, h = handle
    q = _random_quats(30_000, 1)
    odfh, st = be.odf_build(h, "Ti (alpha)", q, np.ones(len(q)), 10.0)
    d, _x, _y = tx.equal_area_grid(9, 25)
    pf = be.odf_pf(odfh, (0, 0, 0, 1), d)
    assert st["texture_index"] < 1.1 and st["max_mrd"] < 1.6
    assert np.abs(pf - 1).max() < 0.25


def test_specimen_frame_from_sheets(proj):
    fr = tx.frame_for(proj, MID)
    assert fr.kind == "specimen" and fr.labels["x"].startswith("beam axis")
    assert np.allclose(fr.R, [[1, 0, 0], [0, -1, 0], [0, 0, -1]])          # +x -> +X, +y -> -Y, z -> -Z
    assert "BD" in fr.directions and "map +x = +X" in fr.text
    q = tx.quat_from_matrix(fr.R)
    assert np.allclose(el.rotation_matrix(q), fr.R, atol=1e-12)


def test_texture_step_counts_grains_and_warns(proj):
    s = service.run(proj, "texture.run", {"map_id": MID, "weighting": "grain"})
    summ = json.loads((proj.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    assert summ["n_orientations"] == summ["n_grains"] > 100
    assert summ["texture_index"] > 1 and summ["max_mrd"] > 1
    assert any("statistics are uncertain" in w for w in summ["warnings"])           # few grains >= 5 um
    assert set(summ["pf_max_mrd"]) == {"{0001}", "{10-10}", "{11-20}", "{10-11}"}
    assert (proj.root / s.outputs["pf"].path).is_file() and (proj.root / s.outputs["ipf"].path).is_file()
    step, entry = service.export(proj, "texture.export_odf", {"texture": s.id})
    txt = (proj.root / step.outputs["export"].path).read_text(encoding="utf-8")
    assert txt.startswith("% MTEX ODF") and entry.op == "texture.export_odf"
    f = service.render_figure_as(proj, s.id, "svg", "ipf")
    assert f.suffix == ".svg" and f.stat().st_size > 1000
    rep = service.replay(proj)
    assert rep.ok, [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]


def test_beta_texture_needs_reconstruction(proj):
    with pytest.raises(tx.TextureInputError, match="no prior-beta reconstruction"):
        service.run(proj, "texture.run", {"map_id": MID, "phase": "beta"})
