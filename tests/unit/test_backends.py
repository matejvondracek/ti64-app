import json

import pytest

from ti64app import cli
from ti64app.backends import backend_names, backend_status, get_backend
from ti64app.backends.base import AnalysisBackend, BackendFeatureUnavailable, LoadParams


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
    reason = get_backend("matlab").unavailable_reason()
    assert isinstance(reason, str)          # '' when available, otherwise shown as 'unavailable'


def test_load_not_implemented_yet(tmp_path):
    with pytest.raises(BackendFeatureUnavailable, match="pymtex"):
        get_backend("pymtex").load_ebsd(LoadParams(file=tmp_path / "x.ang", input_state="raw"))


def test_cli_backends(capsys):
    assert cli.main(["backends"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == set(backend_status())
