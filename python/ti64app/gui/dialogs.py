"""Windows file dialogs: native (pywebview window) in the desktop app, tkinter in --browser mode on this machine,
and a queue of answers in tests.

Tests: set MOCK (simulated user, same process) or the environment variable TI64_MOCK_DIALOGS to a JSON file
{"queue": [answer, ...]} (Playwright, separate server process). Each dialog pops the first answer: a list of paths
(open files), a path (folder / save) or null (cancelled). Every call is appended to "calls" in that file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from nicegui import run

MOCK: list | None = None                  # simulated-user tests
CALLS: list[dict] = []
WINDOW = {"w": None}                      # pywebview window (desktop.py)


def _mock(kind: str, **kw):
    """(True, answer) if dialogs are mocked, else (False, None)."""
    CALLS.append({"kind": kind, **kw})
    if MOCK is not None:
        return True, (MOCK.pop(0) if MOCK else None)
    f = os.environ.get("TI64_MOCK_DIALOGS")
    if f:
        p = Path(f)
        d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"queue": []}
        ans = d["queue"].pop(0) if d.get("queue") else None
        d.setdefault("calls", []).append({"kind": kind, **kw})
        p.write_text(json.dumps(d, indent=1), encoding="utf-8")
        return True, ans
    return False, None


def _types_tk(types: list[tuple[str, str]] | None, all_first: bool = False):
    t = [(name, pat.replace(";", " ")) for name, pat in (types or [])]
    return [("All files", "*.*")] + t if all_first else t + [("All files", "*.*")]


def _types_webview(types: list[tuple[str, str]] | None, all_first: bool = False) -> tuple[str, ...]:
    """The first entry is the filter the Windows dialog starts with."""
    t = tuple(f"{name} ({pat})" for name, pat in (types or []))
    return ("All files (*.*)",) + t if all_first else t + ("All files (*.*)",)


def _tk(kind: str, title: str, directory: str, types, filename: str = "", all_first: bool = False):
    import tkinter as tk  # noqa: PLC0415
    from tkinter import filedialog  # noqa: PLC0415

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "open":
            r = filedialog.askopenfilenames(parent=root, title=title, initialdir=directory or None,
                                            filetypes=_types_tk(types, all_first))
            return [str(Path(x)) for x in r] if r else None
        if kind == "folder":
            r = filedialog.askdirectory(parent=root, title=title, initialdir=directory or None, mustexist=False)
            return str(Path(r)) if r else None
        r = filedialog.asksaveasfilename(parent=root, title=title, initialdir=directory or None,
                                         initialfile=filename, filetypes=_types_tk(types),
                                         defaultextension=Path(filename).suffix or None)
        return str(Path(r)) if r else None
    finally:
        root.destroy()


async def _native(kind: str, title: str, directory: str, types, filename: str = "", multiple: bool = True,
                  all_first: bool = False):
    import webview  # noqa: PLC0415

    w = WINDOW["w"]
    mode = {"open": webview.FileDialog.OPEN, "folder": webview.FileDialog.FOLDER,
            "save": webview.FileDialog.SAVE}[kind]
    kw = {"directory": directory or "", "allow_multiple": multiple and kind == "open"}
    if kind != "folder":
        kw["file_types"] = _types_webview(types, all_first)
    if kind == "save":
        kw["save_filename"] = filename
    r = await run.io_bound(w.create_file_dialog, mode, **kw)
    if not r:
        return None
    if isinstance(r, str):
        r = [r]
    r = [str(Path(x)) for x in r]
    return r if kind == "open" else r[0]


async def _ask(kind: str, title: str, directory: str = "", types=None, filename: str = "", multiple: bool = True,
               all_first: bool = False):
    mocked, ans = _mock(kind, title=title, directory=directory, filename=filename,
                        types=[t[0] for t in (types or [])], all_first=all_first)
    if mocked:
        return ans
    if WINDOW["w"] is not None:
        return await _native(kind, title, directory, types, filename, multiple, all_first)
    return await run.io_bound(_tk, kind, title, directory, types, filename, all_first)


async def open_files(title: str = "Add files", directory: str = "", types=None, multiple: bool = True,
                     all_first: bool = False) -> list[str]:
    """all_first: the dialog starts with the filter 'All files (*.*)' (Add file)."""
    r = await _ask("open", title, directory, types, multiple=multiple, all_first=all_first)
    return list(r or [])


async def pick_folder(title: str = "Choose a folder", directory: str = "") -> str | None:
    return await _ask("folder", title, directory)


async def save_file(title: str = "Save as", filename: str = "", directory: str = "", types=None) -> str | None:
    return await _ask("save", title, directory, types, filename)


FILE_TYPES = [("EBSD maps", "*.ang"), ("SEM images", "*.bmp;*.tif;*.tiff;*.png;*.jpg"), ("EDAX patterns", "*.up2")]
