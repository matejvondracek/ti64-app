"""pymtex facts the backend relies on (docs/data_audit.md), checked on the 300x300 crops."""
import numpy as np
import pytest

mtex = pytest.importorskip("mtex")

from ti64app.backends.pymtex_plot import ipf_key  # noqa: E402
from ti64app.settings import FigureStyle, IpfKeySetting  # noqa: E402


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
    cols = {}
    for d in ("X", "Z"):
        style = FigureStyle(ipf={"Ti (alpha)": IpfKeySetting(key="ipfHKLKey", direction=d)})
        cols[d] = np.asarray(ipf_key(a.CS, style, "Ti (alpha)").orientation2color(ori))
    assert not np.allclose(cols["X"], cols["Z"])


def test_matlab_property_name_is_ignored(crop300_ju02):
    """Setting the MATLAB name inversePoleFigureDirection does nothing in pymtex 1.0.0."""
    a = mtex.loadEBSD(str(crop300_ju02), setting=2)["Ti (alpha)"]
    ori = a.orientations[:2000]
    k1 = mtex.ipfHKLKey(a.CS)
    k2 = mtex.ipfHKLKey(a.CS)
    k2.inversePoleFigureDirection = mtex.vector3d.X
    np.testing.assert_allclose(np.asarray(k1.orientation2color(ori)), np.asarray(k2.orientation2color(ori)))
