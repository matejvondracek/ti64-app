"""MATLAB engine lifecycle (fix 6 of the phase 6.6 user test): headless start, crash detection, one restart with a
plain-language message, no hang on a dead engine. Mocked engine (always runs) + the real MATLAB (slow, optional)."""
from __future__ import annotations

import os
import sys
import threading
import time
import types

import pytest

from ti64app.backends import matlab_backend as mb
from ti64app.backends.base import EngineRestarted


# ---- fake matlab.engine --------------------------------------------------------------------------
class EngineError(Exception):
    pass


class RejectedExecutionError(Exception):
    pass


class TimeoutError_(Exception):
    pass


class CancelledError(Exception):
    pass


class FakeFuture:
    def __init__(self, fn, delay=0.0):
        self._fn, self._t = fn, time.monotonic() + delay
        self._cancelled = False

    def done(self):
        return time.monotonic() >= self._t

    def result(self, timeout=None):
        if timeout is not None and not self.done():
            time.sleep(min(timeout, max(0.0, self._t - time.monotonic())))
            if not self.done():
                raise TimeoutError_()
        if self._cancelled:
            raise CancelledError()
        return self._fn()

    def cancel(self):
        self._cancelled = True
        return True


class FakeEngine:
    """One fake MATLAB process: `alive` is what psutil would report; `hang` = crashed but alive (crash dialog)."""
    n_started = 0

    def __init__(self):
        FakeEngine.n_started += 1
        self.pid = 40000 + FakeEngine.n_started
        self.alive = True
        self.store: list[str] = []
        self.hang = False
        self.ti64 = types.SimpleNamespace(setupMtex=lambda *a, **k: None)

    def feature(self, what):
        return float(self.pid)

    def addpath(self, *a, **k):
        pass

    def quit(self):
        self.alive = False

    def feval(self, fn, *args, nargout=1, stdout=None, stderr=None, background=False):
        if not self.alive:
            raise EngineError("Unknown exception")

        def run():
            if not self.alive:
                raise EngineError("Unknown exception")
            if fn == "ti64.store" and args[0] == "list":
                return list(self.store)
            if fn == "ti64.store" and args[0] == "put":
                self.store.append(f"id{len(self.store)}")
                return self.store[-1]
            if fn == "die":
                self.alive = False
                raise EngineError("Unknown exception")
            return 42.0
        delay = 3600.0 if (fn == "hang" or self.hang) else 0.0
        return FakeFuture(run, delay)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    engines: list[FakeEngine] = []
    starts: list[str] = []

    def start_matlab(option="-nodesktop", background=False, **kw):
        starts.append(option)
        e = FakeEngine()
        engines.append(e)
        return FakeFuture(lambda: e) if background else e

    me = types.SimpleNamespace(start_matlab=start_matlab, EngineError=EngineError,
                               RejectedExecutionError=RejectedExecutionError, TimeoutError=TimeoutError_,
                               CancelledError=CancelledError, _engines=[])
    be = mb.MatlabBackend()
    monkeypatch.setattr(mb.MatlabBackend, "_engine_module", staticmethod(lambda: me))
    monkeypatch.setattr(be, "unavailable_reason", lambda: "")
    monkeypatch.setattr(mb, "logs_dir", lambda: tmp_path)
    import importlib  # noqa: PLC0415  (GUI tests may re-import the package: patch the module object itself)

    monkeypatch.setattr(importlib.import_module("ti64app.logs"), "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(mb, "load_settings", lambda: types.SimpleNamespace(paths=types.SimpleNamespace(mtex_path="x")))
    monkeypatch.setattr(mb, "WATCH_S", 0.05)
    by_pid = lambda pid: next((e for e in engines if e.pid == pid), None)  # noqa: E731
    monkeypatch.setattr(mb, "_create_time", lambda pid: 1000.0 + pid if (by_pid(pid) and by_pid(pid).alive) else 0.0)

    def kill(pid):
        by_pid(pid).alive = False
    monkeypatch.setattr("psutil.Process", lambda pid: types.SimpleNamespace(kill=lambda: kill(pid)))
    return types.SimpleNamespace(be=be, engines=engines, starts=starts, logs=tmp_path)


def test_starts_headless_new_process_with_logfile(fake):
    assert fake.be.call("version") == 42.0
    opt = fake.starts[0]
    assert "-nodesktop" in opt and "-nosplash" in opt and "-logfile" in opt and str(fake.logs) in opt
    assert os.environ["MATLAB_LOG_DIR"] == str(fake.logs)
    assert fake.be.generation == 1


def test_never_attaches_to_a_shared_session():
    src = mb.__file__
    text = open(src, encoding="utf-8").read()
    assert "connect_matlab" not in text.replace("no connect_matlab", "") and "find_matlab" not in text.replace(
        "find_matlab /", "")


def test_engine_error_with_dead_process_restarts_once_and_reports(fake):
    be = fake.be
    be.call("ti64.store", "put")
    assert be.handles() == ["id0"]
    with pytest.raises(EngineRestarted) as ei:
        be.call("die")
    msg = str(ei.value)
    assert msg.startswith("MATLAB stopped unexpectedly") and "was restarted" in msg and "run the step again" in msg
    assert len(fake.engines) == 2 and fake.engines[1].alive and be.restarts == 1 and be.generation == 2
    assert be.handles() == []                  # old objects gone -> service.materialize rebuilds them
    assert be.call("version") == 42.0          # the new engine works
    assert be.pop_notice() == msg and be.pop_notice() is None


def test_process_killed_during_a_long_call_does_not_hang(fake):
    be = fake.be
    be.engine()
    threading.Timer(0.2, lambda: setattr(fake.engines[0], "alive", False)).start()
    t = time.monotonic()
    with pytest.raises(EngineRestarted, match="MATLAB process ended"):
        be.call("hang")
    assert time.monotonic() - t < 5


def test_crash_dump_of_a_hung_process_kills_and_restarts(fake):
    be = fake.be
    be.engine()
    eng = fake.engines[0]
    eng.hang = True
    threading.Timer(0.2, lambda: (fake.logs / f"matlab_crash_dump.{eng.pid}-1").write_text("Stack Trace")).start()
    with pytest.raises(EngineRestarted) as ei:
        be.call("anything")
    assert not eng.alive                       # the crashed (still running) process was killed
    assert ei.value.crash_dump and ei.value.crash_dump.endswith(f"matlab_crash_dump.{eng.pid}-1")
    assert "Crash report" in str(ei.value)


def test_engine_error_while_matlab_alive_is_reraised(fake):
    be = fake.be
    be.engine()
    fake.engines[0].feval = lambda *a, **k: (_ for _ in ()).throw(EngineError("bad argument"))
    with pytest.raises(EngineError):
        be.call("x")
    assert be.restarts == 0


def test_start_timeout_reports_plainly(fake, monkeypatch):
    be = fake.be
    me = mb.MatlabBackend._engine_module()
    monkeypatch.setattr(mb, "START_TIMEOUT_S", 0.1)
    monkeypatch.setattr(me, "start_matlab", lambda *a, **k: FakeFuture(lambda: None, delay=3600))
    with pytest.raises(mb.BackendUnavailable, match="did not start within"):
        be.engine()


# ---- real MATLAB (slow) ----------------------------------------------------------------------------
@pytest.mark.slow
def test_real_matlab_killed_mid_session_recovers(tmp_path, monkeypatch):
    be = mb.MatlabBackend()
    if be.unavailable_reason():
        pytest.skip(be.unavailable_reason())
    import psutil

    try:
        be.engine()
        pid = be._pid
        assert psutil.Process(pid).name().lower().startswith("matlab")
        hid = be.call("ti64.store", "put", 1.0)
        assert hid in be.handles()
        threading.Timer(2.0, lambda: psutil.Process(pid).kill()).start()
        t = time.monotonic()
        with pytest.raises(EngineRestarted, match="was restarted"):
            be.call("pause", 30.0, nargout=0)
        assert time.monotonic() - t < 120
        assert be._pid != pid and be.restarts == 1
        assert hid not in be.handles()
        assert float(be.call("plus", 1.0, 2.0)) == 3.0
        assert be.log_file and be.log_file.is_file()
    finally:
        be.close()
