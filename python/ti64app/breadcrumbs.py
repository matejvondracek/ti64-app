"""Breadcrumb log of the UI and the errors since start, for "Save report for Claude" (phase 6.6 fix 4, report.py).

- add(kind, text): one UI action with a timestamp (clicks, tab changes, dialogs, recipe steps); the newest 300 kept.
- record_error(what, exc): an error with its full traceback (paths shortened to file names); also every log record
  of level ERROR with an exception (ErrorCollector on the root logger, installed by the GUI and the CLI / MCP server).
Nothing here is written to disk; report.py writes it when the user asks.
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from datetime import datetime

from .feedback import shorten_paths

STARTED = datetime.now()
_CRUMBS: deque[dict] = deque(maxlen=300)
_ERRORS: deque[dict] = deque(maxlen=200)
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def add(kind: str, text: str) -> None:
    """kind: click / tab / dialog / step / notify / ...; text is shortened (no absolute paths)."""
    t = shorten_paths(str(text))[:300]
    with _LOCK:
        _CRUMBS.append({"time": _now(), "kind": kind, "text": t})
    logging.getLogger("ti64.ui").info("%s: %s", kind, t)          # also in the log file (its tail goes in the report)


def last(n: int = 30) -> list[dict]:
    with _LOCK:
        return list(_CRUMBS)[-n:]


def record_error(what: str, exc: BaseException | None = None, tb: str | None = None) -> None:
    if tb is None and exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    with _LOCK:
        _ERRORS.append({"time": _now(), "what": shorten_paths(what or ""),
                        "type": type(exc).__name__ if exc is not None else "",
                        "message": shorten_paths(str(exc) if exc is not None else ""),
                        "traceback": shorten_paths(tb or "")})


def errors() -> list[dict]:
    with _LOCK:
        return list(_ERRORS)


def clear() -> None:
    with _LOCK:
        _CRUMBS.clear()
        _ERRORS.clear()


class ErrorCollector(logging.Handler):
    """Root-logger handler: every ERROR record with exc_info goes to the error list (full traceback)."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.exc_info and record.exc_info[1] is not None:
                e = record.exc_info[1]
                tb = "".join(traceback.format_exception(*record.exc_info))
                record_error(record.getMessage(), e, tb)
            else:
                record_error(record.getMessage(), None, "")
        except Exception:  # noqa: BLE001
            pass


def install() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, ErrorCollector) for h in root.handlers):
        root.addHandler(ErrorCollector())
