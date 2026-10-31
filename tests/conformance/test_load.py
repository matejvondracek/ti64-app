"""ebsd.load conformance: pymtex and MATLAB/MTEX 7.2 give the same per-pixel data (file order).

Tolerances (docs/data_audit.md): identical pixel set, phases, CI, IQ; orientations within 1e-4 deg
(quaternions compared up to sign).
"""
import numpy as np
import pytest

from ti64app import service
from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams
from tests.conftest import sample_params

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


def rotation_angle_deg(qa, qb):
    dot = np.abs(np.sum(qa * qb, axis=1)).clip(0, 1)
    return np.degrees(2 * np.arccos(dot))


def test_versions(matlab):
    v = matlab.versions()
    assert v["mtex"].startswith("mtex-7.2") and v["matlab"].startswith("25.2")
    assert get_backend("pymtex").versions()["mtex"] == "1.0.0"


def test_load_conformance(matlab, crop300):
    lp = LoadParams(file=crop300, input_state="oim_cleaned")
    py = get_backend("pymtex")
    a = py.get_ebsd(py.load_ebsd(lp))
    h = matlab.load_ebsd(lp)
    assert h.id in matlab.handles()                    # object stays in the MATLAB session
    b = matlab.get_ebsd(h)
    assert a.n == b.n == h.n_points == 90000
    assert (a.grid, a.setting, a.phase_names) == (b.grid, b.setting, b.phase_names)
    assert a.step_um == pytest.approx(b.step_um)
    np.testing.assert_allclose(a.x, b.x, atol=1e-5)
    np.testing.assert_allclose(a.y, b.y, atol=1e-5)
    np.testing.assert_array_equal(a.phase, b.phase)
    for k in ("ci", "iq", "fit", "sem"):
        np.testing.assert_allclose(a.props[k], b.props[k], err_msg=k)
    m = a.phase > 0
    np.testing.assert_array_equal(np.isnan(a.quat[:, 0]), np.isnan(b.quat[:, 0]))
    assert rotation_angle_deg(a.quat[m], b.quat[m]).max() < 1e-4
    matlab.release(h)
    assert h.id not in matlab.handles()


def test_store_missing_id_errors(matlab):
    from ti64app.backends.base import EBSDHandle

    with pytest.raises(Exception, match="No object with ID"):
        matlab.get_ebsd(EBSDHandle("matlab", "doesnotexist", 0))


def test_replay_with_other_backend(matlab, project, crop300_ju02):
    service.run(project, "sample.set", sample_params(crop300_ju02))
    service.run(project, "ebsd.load", {"map_id": "JU02_c300"})                    # pymtex
    same = service.replay(project)
    assert same.ok and {c.status for c in same.checks} == {"identical"}
    other = service.replay(project, backend="matlab")
    assert other.ok, other
    st = {c.output: c.status for c in other.checks if c.op == "ebsd.load"}
    assert st["ebsd"] == "within_tolerance"            # float rounding / quaternion sign differ
