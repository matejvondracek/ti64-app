import json

import numpy as np
import pytest

from ti64app import cli
from ti64app.backends import backend_names, backend_status, get_backend
from ti64app.backends.base import AnalysisBackend, LoadParams


def test_registry():
    assert backend_names() == ["pymtex", "matlab"]
    for n in backend_names():
        assert isinstance(get_backend(n), AnalysisBackend)
    with pytest.raises(KeyError):
        get_backend("octave")


def test_pymtex_available_and_pinned():
    be = get_backend("pymtex")
    assert be.is_available()
    assert be.versions()["mtex"] == "1.0.0"


def test_matlab_status_never_raises():
    reason = get_backend("matlab").unavailable_reason()   # static check: no engine start
    assert isinstance(reason, str)


def test_matlab_unavailable_without_mtex_path(tmp_path, monkeypatch):
    f = tmp_path / "s.toml"
    f.write_text("[paths]\nmtex_path = ''\n", encoding="utf-8")
    monkeypatch.setenv("TI64_SETTINGS", str(f))
    from ti64app.backends.matlab_backend import MatlabBackend

    be = MatlabBackend()
    assert not be.is_available() and be.unavailable_reason()


def _raw(path):
    d = np.loadtxt(path, comments="#", usecols=range(10))
    ph = d[:, 7].astype(int)
    ph[ph < 0] = 0
    return d, ph


def test_pymtex_load_crop_file_order(crop300):
    be = get_backend("pymtex")
    h = be.load_ebsd(LoadParams(file=crop300, input_state="oim_cleaned"))
    assert h.n_points == 90000 and h.id in be.handles()
    d = be.get_ebsd(h)
    raw, ph = _raw(crop300)
    assert d.n == 90000 and d.grid == "hex" and d.step_um == pytest.approx(0.2) and d.setting == 2
    np.testing.assert_allclose(d.x, raw[:, 3], atol=1e-4)
    np.testing.assert_allclose(d.y, raw[:, 4], atol=1e-4)
    np.testing.assert_array_equal(d.phase, ph)
    np.testing.assert_allclose(d.props["ci"], raw[:, 6])
    np.testing.assert_allclose(d.props["iq"], raw[:, 5])
    assert set(d.props) == {"ci", "iq", "fit", "sem"}
    assert d.phase_names == {1: "Ti (alpha)", 2: "Ti (beta)"}
    assert np.isnan(d.quat[ph == 0]).all() and not np.isnan(d.quat[ph > 0]).any()
    be.release(h)
    assert h.id not in be.handles()


def test_cli_backends(capsys):
    assert cli.main(["backends"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == set(backend_status())
