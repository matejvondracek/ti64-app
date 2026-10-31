"""Conformance tests run every core operation on both backends and compare numbers within stated
tolerances (CLAUDE.md). Operations are added here as they are implemented; for now: both backends
start and report the pinned versions."""
import pytest

from ti64app.backends import get_backend

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


def test_versions(matlab):
    v = matlab.versions()
    assert v["mtex"].startswith("mtex-7.2")
    assert v["matlab"].startswith("25.2")
    assert get_backend("pymtex").versions()["mtex"] == "1.0.0"
