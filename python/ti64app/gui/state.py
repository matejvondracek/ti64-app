"""Per-window GUI state: the open project (one window = one project), the save status shown in the top bar and the
autosaved working state (gui_state.json in the project: tab, selected file, viewer zoom / position, overlays,
unapplied drafts, export choices)."""
from __future__ import annotations

import functools
import inspect
import logging
import threading
import time
from pathlib import Path

from ..project import Project, add_recent, resolve_project

log = logging.getLogger("ti64app.gui")

STATE: dict = {"project": None}          # project root (str) or name under projects_dir()


def current_project() -> Project | None:
    root = resolve_project(STATE.get("project"))
    if root is None or not (root / "project.json").is_file():
        return None
    try:
        return Project.open(root)
    except Exception:  # noqa: BLE001
        log.exception("cannot open project %s", root)
        return None


def open_project(root: str | Path) -> Project:
    p = Project.open(Path(root))
    STATE["project"] = str(p.root)
    add_recent(p.root)
    return p


class SaveStatus:
    """'Saved' / 'Saving...' / plain-language error, bound to the label in the top bar."""

    def __init__(self) -> None:
        self.text = "Saved"
        self.error = ""
        self._n = 0
        self._lock = threading.Lock()

    def begin(self) -> None:
        with self._lock:
            self._n += 1
            self.text = "Saving..."

    def end(self, error: Exception | None = None) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)
            if error is not None:
                self.error = plain_error(error)
                self.text = f"Not saved: {self.error}"
            elif self._n == 0:
                self.error = ""
                self.text = "Saved"


SAVE = SaveStatus()


def autosave(fn, *args, **kwargs):
    """Run a write (project state) and reflect it in the save indicator. Errors are logged and re-raised."""
    SAVE.begin()
    try:
        out = fn(*args, **kwargs)
    except Exception as e:
        log.exception("autosave failed")
        SAVE.end(e)
        raise
    SAVE.end()
    return out


class GuiState:
    """gui_state.json of the open project, written (atomically) at most every 0.4 s."""

    def __init__(self, project: Project) -> None:
        self.p = project
        self.d = project.gui_state()
        self._due = 0.0

    def get(self, *keys, default=None):
        d = self.d
        for k in keys:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d

    def set(self, *keys_and_value) -> None:
        *keys, value = keys_and_value
        d = self.d
        for k in keys[:-1]:
            # an older gui_state.json may hold a plain value where a dict is expected now (e.g. layer.<map> = "phase"
            # before the map tabs): replace it, else the EBSD tab never showed the map after loading (fix 3.8)
            if not isinstance(d.get(k), dict):
                d[k] = {}
            d = d[k]
        if d.get(keys[-1]) == value:
            return
        d[keys[-1]] = value
        self.save()

    def pop(self, *keys) -> None:
        d = self.d
        for k in keys[:-1]:
            d = d.get(k, {})
        if isinstance(d, dict) and keys[-1] in d:
            del d[keys[-1]]
            self.save()

    def save(self) -> None:
        try:
            autosave(self.p.save_gui_state, self.d)
        except Exception:  # noqa: BLE001  (shown in the save indicator)
            pass


def plain_error(e: BaseException) -> str:
    """An exception as a short plain-language sentence for a notification."""
    msg = str(e).strip().strip("'\"") or type(e).__name__
    if isinstance(e, FileNotFoundError):
        return f"File or folder not found: {e.filename or msg}"
    if isinstance(e, PermissionError):
        return f"No permission to write {e.filename or msg} (is it open in another program?)"
    if isinstance(e, MemoryError):
        return "Not enough memory for this step; try a smaller map or close other programs."
    if isinstance(e, KeyError):
        return msg
    if type(e).__name__ == "ValidationError":
        lines = [ln for ln in msg.splitlines() if ln and not ln.startswith(("For further", " "))]
        return "Invalid value: " + "; ".join(lines[1:3] or lines[:1])
    if type(e).__name__ == "BackendUnavailable":
        return msg + " (choose another backend in Settings)"
    return msg[:400]


def now() -> float:
    return time.time()


def stable(fn):
    """Event handler that keeps working after an `await` (Windows dialog, long step) even when the element that
    triggered it was removed in the meantime, e.g. a button inside a list that was refreshed while the dialog was open
    ("The parent element this slot belongs to has been deleted"). The handler runs in the page's root slot
    (client.layout), so notifications, dialogs and refreshes after the await still reach the page. Call it while the
    page is being built (it captures the page's client)."""
    from nicegui import context  # noqa: PLC0415

    client = context.client

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with client.layout:
            r = fn(*args, **kwargs)
            if inspect.isawaitable(r):
                r = await r
            return r
    return wrapper
