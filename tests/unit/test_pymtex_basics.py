"""pymtex facts the backend relies on (docs/data_audit.md), checked on the 300x300 crops."""
import numpy as np
import pytest

mtex = pytest.importorskip("mtex")

from ti64app.backends.pymtex_plot import make_key  # noqa: E402


def test_crop_loads_as_hex_grid(crop300):
    e = mtex.loadEBSD(str(crop300), setting=2)
    assert tuple(e.shape) == (300, 300)
    pad = e.isPadding() if callable(e.isPadding) else e.isPadding
    assert int(np.sum(pad)) == 0
    assert sorted(e.prop.keys()) == ["ci", "fit", "iq", "sem_signal"]
    assert str(e.how2plot) == "y↓→x"          # east = x, out of screen = -z


def test_ipf_direction_is_applied(crop300_ju02):
    a = mtex.loadEBSD(str(crop300_ju02), setting=2)["Ti (alpha)"]
    ori = a.orientations[:2000]
    cx = np.asarray(make_key(a.CS, "ipfHKLKey", (1, 0, 0)).orientation2color(ori))
    cz = np.asarray(make_key(a.CS, "ipfHKLKey", (0, 0, 1)).orientation2color(ori))
    assert not np.allclose(cx, cz)


def test_matlab_property_name_is_ignored(crop300_ju02):
    """Setting the MATLAB name inversePoleFigureDirection does nothing in pymtex 1.0.0."""
    a = mtex.loadEBSD(str(crop300_ju02), setting=2)["Ti (alpha)"]
    ori = a.orientations[:2000]
    k1 = mtex.ipfHKLKey(a.CS)
    k2 = mtex.ipfHKLKey(a.CS)
    k2.inversePoleFigureDirection = mtex.vector3d.X
    np.testing.assert_allclose(np.asarray(k1.orientation2color(ori)), np.asarray(k2.orientation2color(ori)))


def test_alpha0_keeps_voids_as_grains(crop300_ju02):
    """MTEX 7 / pymtex calcGrains fills small voids unless alpha = 0 (decisions D7)."""
    e = mtex.loadEBSD(str(crop300_ju02), setting=2)
    m = np.asarray(e.prop["ci"]).ravel() < 0.1
    e.rotations.data.reshape(-1, 4)[m] = np.nan
    e.phaseId.reshape(-1)[m] = 0
    g0 = mtex.calcGrains(e, angle=3 * mtex.degree, alpha=0)
    g3 = mtex.calcGrains(e, angle=3 * mtex.degree, alpha=3.1)
    n0 = int((~np.asarray(g0.isIndexed)).sum())
    n3 = int((~np.asarray(g3.isIndexed)).sum())
    assert n0 == 622 and n3 < 10
