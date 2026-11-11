"""Feedback button (fix 3.2): a report with the description, category, app version, backend, OS and the last 100 log
lines (paths shortened to file names, because GitHub issues are public), as a pre-filled GitHub issue or e-mail, and
as a .zip (report + log tail + optional window screenshot) to attach.

GitHub's "new issue" URL and mailto: links have length limits (browsers / mail clients cut at ~2-8 kB), so the URL
bodies carry the report with a shortened log; the .zip always has the full 100 lines.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from . import __version__
from .settings import logs_dir

CATEGORIES = {"bug": "Bug", "idea": "Idea", "question": "Question"}
URL_LOG_LINES = 25                  # log lines inside the URL body (the .zip has all of them)
MAX_URL = 7000

# Windows (C:\...\file.ext, \\server\share\...) and POSIX (/a/b/file.ext) paths -> file name only
_WIN = re.compile(r"(?:(?<![\w\\])[A-Za-z]:|(?<!\\)\\\\[^\\/\s]+)[\\/](?:[^\\/:*?\"<>|\r\n]+[\\/])*([^\\/:*?\"<>|\r\n\s']*)")
_POSIX = re.compile(r"(?<![\w.:/])/(?:[^/\s'\"]+/)+([^/\s'\"]*)")


def shorten_paths(text: str) -> str:
    """Replace absolute paths by their file name (user names, folders and projects stay private)."""
    text = _WIN.sub(lambda m: m.group(1) or "…", text)
    return _POSIX.sub(lambda m: m.group(1) or "…", text)


def log_tail(n: int = 100, log_file: Path | None = None) -> list[str]:
    """Last n lines of the GUI log, paths shortened."""
    f = log_file or (logs_dir() / "ti64-gui.log")
    if not f.is_file():
        return []
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []
    return [shorten_paths(ln) for ln in lines]


def subject(category: str) -> str:
    """E-mail subject: exactly "[Ti64] Bug", "[Ti64] Idea" or "[Ti64] Question"."""
    return f"[Ti64] {CATEGORIES[category]}"


def build_report(description: str, category: str, backend: str, log_lines: list[str] | None = None,
                 system: dict | None = None) -> dict:
    """{title, body (markdown, full log), body_short (for URLs), meta}."""
    from .sysinfo import system_summary  # noqa: PLC0415

    system = system or system_summary()
    log_lines = log_tail() if log_lines is None else [shorten_paths(ln) for ln in log_lines]
    desc = shorten_paths((description or "").strip())
    first = desc.splitlines()[0][:80] if desc else ""
    title = f"[{CATEGORIES[category]}] {first}" if first else f"[{CATEGORIES[category]}] (no description)"
    meta = {"category": CATEGORIES[category], "app version": __version__, "backend": backend, "OS": system["os"],
            "Python": system["python"], "RAM": system["ram"], "date": datetime.now().isoformat(timespec="seconds")}
    head = (f"{desc or '(no description)'}\n\n---\n" + "\n".join(f"- {k}: {v}" for k, v in meta.items()))

    def with_log(lines):
        if not lines:
            return head + "\n\n(no log lines)"
        return head + f"\n\n<details><summary>last {len(lines)} log lines (paths shortened)</summary>\n\n```\n" + \
            "\n".join(lines) + "\n```\n</details>"
    return {"title": title, "body": with_log(log_lines), "body_short": with_log(log_lines[-URL_LOG_LINES:]),
            "meta": meta, "log": log_lines}


def github_issue_url(repo_url: str, report: dict) -> str:
    """https://github.com/<owner>/<repo>/issues/new?title=...&body=... (body cut to the URL limit)."""
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    body = report["body_short"]
    url = f"{base}/issues/new?" + urlencode({"title": report["title"], "body": body})
    while len(url) > MAX_URL and len(body) > 200:
        body = body[: int(len(body) * 0.8)] + "\n…(cut; see the attached .zip)"
        url = f"{base}/issues/new?" + urlencode({"title": report["title"], "body": body})
    return url


def mailto_url(email: str, category: str, report: dict, zip_name: str = "") -> str:
    body = report["body_short"]
    if zip_name:
        body = f"(Please attach {zip_name}: it holds the full report.)\n\n" + body

    def make(b):
        return f"mailto:{email}?subject={quote(subject(category))}&body={quote(b)}"
    url = make(body)
    while len(url) > MAX_URL and len(body) > 200:
        body = body[: int(len(body) * 0.8)] + "\n…(cut; see the attached .zip)"
        url = make(body)
    return url


def write_zip(folder: Path, report: dict, screenshot_png: bytes | None = None) -> Path:
    """ti64_feedback_<date>.zip with report.md, log_tail.txt and (only if given) screenshot.png."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"ti64_feedback_{datetime.now():%Y%m%d_%H%M%S}.zip"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.md", f"# {report['title']}\n\n{report['body']}\n")
        z.writestr("log_tail.txt", "\n".join(report["log"]) + "\n")
        if screenshot_png:
            z.writestr("screenshot.png", screenshot_png)
    return f


def window_screenshot() -> bytes | None:
    """PNG of this app's own window only (Windows: the visible top-level window of this process, i.e. the native
    desktop window). None when there is none (browser mode) or capturing fails."""
    import io  # noqa: PLC0415
    import os  # noqa: PLC0415

    if os.name != "nt":
        return None
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        from PIL import ImageGrab  # noqa: PLC0415

        user32 = ctypes.windll.user32
        pid = os.getpid()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _l):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                found.append(hwnd)
            return True
        user32.EnumWindows(cb, 0)
        if not found:
            return None
        rect = wintypes.RECT()
        try:                                    # DWMWA_EXTENDED_FRAME_BOUNDS: without the invisible resize border
            ctypes.windll.dwmapi.DwmGetWindowAttribute(found[0], 9, ctypes.byref(rect), ctypes.sizeof(rect))
        except Exception:  # noqa: BLE001
            user32.GetWindowRect(found[0], ctypes.byref(rect))
        if rect.right - rect.left < 10 or rect.bottom - rect.top < 10:
            return None
        img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
