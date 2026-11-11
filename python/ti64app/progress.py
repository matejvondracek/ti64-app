"""Stage progress of long actions for the GUI (fix 3.7: loading shows "reading file / building grid / cleanup from
cache / drawing" in the map area).

Code that does the work calls `report(stage, frac)`; the GUI runs the work inside `channel(name)` (or with
`run_in(name, fn, ...)` in a worker thread) and polls `get(name)` with a timer. Without a channel, report() does
nothing, so ops, CLI and MCP are unaffected. Stage names are plain language, shown as is.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}
_LOCAL = threading.local()


@contextmanager
def channel(name: str):
    """Reports of this thread go to `name` while the block runs."""
    prev = getattr(_LOCAL, "channel", None)
    _LOCAL.channel = name
    try:
        yield
    finally:
        _LOCAL.channel = prev


def run_in(name: str, fn: Callable, *args, **kw) -> Any:
    """fn(*args, **kw) with its reports going to `name` (for ng_run.io_bound)."""
    with channel(name):
        return fn(*args, **kw)


def report(stage: str, frac: float | None = None, detail: str = "") -> None:
    """Current stage (and fraction 0-1 of the whole action if known) of the calling thread's channel."""
    ch = getattr(_LOCAL, "channel", None)
    if ch is None:
        return
    with _LOCK:
        old = _STATE.get(ch) or {}
        stages = list(old.get("stages") or [])
        if not stages or stages[-1][0] != stage:
            stages.append((stage, time.perf_counter()))
        _STATE[ch] = {"stage": stage, "frac": frac, "detail": detail, "t": time.perf_counter(), "stages": stages,
                      "t0": old.get("t0", time.perf_counter())}


def set(name: str, stage: str, frac: float | None = None, detail: str = "") -> None:  # noqa: A001
    """Report a stage on a named channel from any thread (the GUI's own stages, e.g. 'drawing')."""
    with channel(name):
        report(stage, frac, detail)


def get(name: str) -> dict | None:
    with _LOCK:
        d = _STATE.get(name)
        return dict(d) if d else None


def clear(name: str) -> None:
    with _LOCK:
        _STATE.pop(name, None)


def timings(name: str) -> list[tuple[str, float]]:
    """Seconds spent per stage so far (profiling, fix 3.8)."""
    d = get(name)
    if not d:
        return []
    st = d["stages"]
    ends = [t for _s, t in st[1:]] + [time.perf_counter()]
    return [(s, round(e - t, 3)) for (s, t), e in zip(st, ends)]
