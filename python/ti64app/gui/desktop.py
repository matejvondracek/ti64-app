"""Desktop app: `ti64-gui` (Scripts\\ti64-gui.exe in the ti64 env, a gui-script: no console, no conda activate).

One window per process and per project: the NiceGUI server runs in a background thread (ui.run_with + uvicorn on a
free local port) and pywebview shows it in a native window (Microsoft Edge WebView2). Opening a project that is
already open in another window brings that window to the front (lock file <project>/.window.lock with its pid and
port); opening a second project from a window that already shows one starts a new window.

There is no console, so everything goes to %APPDATA%\\ti64app\\logs\\ti64-gui.log; start-up errors are shown in a plain
Windows message box that offers to open the log.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

log = logging.getLogger("ti64app.gui.desktop")

WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
LOCK = ".window.lock"
WIN: dict = {"window": None, "port": None, "locked": None}


# ---- message boxes (work without a window) ---------------------------------------------------------------------
def message_box(text: str, title: str = "Ti64 EBSD/BSE", yes_no: bool = False, error: bool = True) -> bool:
    try:
        import ctypes  # noqa: PLC0415

        flags = (0x04 if yes_no else 0x00) | (0x10 if error else 0x40) | 0x10000     # MB_SETFOREGROUND
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags) == 6          # IDYES
    except Exception:  # noqa: BLE001
        print(text, file=sys.stderr)
        return False


def fatal(text: str, log_file: Path | None) -> None:
    log.error(text)
    if log_file and message_box(text + "\n\nOpen the log file?", yes_no=True):
        os.startfile(str(log_file))      # noqa: S606


# ---- WebView2 ---------------------------------------------------------------------------------------------------
def webview2_version() -> str | None:
    """Installed Microsoft Edge WebView2 runtime version (None if missing). pywebview's Windows backend needs it."""
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return None
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")]
    for hive, key in keys:
        try:
            with winreg.OpenKey(hive, key) as k:
                v, _ = winreg.QueryValueEx(k, "pv")
                if v and v != "0.0.0.0":
                    return str(v)
        except OSError:
            continue
    return None


WEBVIEW2_MISSING = (
    "The Microsoft Edge WebView2 Runtime is not installed. Ti64 EBSD/BSE uses it to draw its window.\n\n"
    f"Install the 'Evergreen Bootstrapper' from {WEBVIEW2_URL} (free, from Microsoft; Windows 11 usually has it "
    "already), then start the app again.\n\nUntil then you can use the app in your web browser: run\n"
    "    ti64 gui --browser\nin the ti64 environment.")


# ---- one window per project ------------------------------------------------------------------------------------
def _alive(pid: int) -> bool:
    try:
        import psutil  # noqa: PLC0415

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        return False


def read_lock(root: Path) -> dict | None:
    f = Path(root) / LOCK
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("pid") == os.getpid() or not _alive(int(d.get("pid", -1))):
        return None
    return d


def acquire(root: Path) -> None:
    from ..project import write_json  # noqa: PLC0415

    release()
    write_json(Path(root) / LOCK, {"pid": os.getpid(), "port": WIN["port"], "since": time.time()})
    WIN["locked"] = Path(root)


def release() -> None:
    r = WIN.get("locked")
    if r:
        try:
            d = json.loads((r / LOCK).read_text(encoding="utf-8"))
            if d.get("pid") == os.getpid():
                (r / LOCK).unlink()
        except (OSError, ValueError):
            pass
    WIN["locked"] = None


def focus_other(lock: dict) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{lock['port']}/api/ti64/focus", timeout=3) as r:  # noqa: S310
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def spawn_window(root: Path) -> None:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    cmd = [str(pyw if pyw.is_file() else exe), "-m", "ti64app.gui", "--project", str(root)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)  # noqa: S603


def request_open(root: Path) -> str:
    """'focused' (open in another window), 'spawned' (new window started) or 'here'."""
    from ..project import resolve_project  # noqa: PLC0415
    from .state import STATE  # noqa: PLC0415

    root = Path(root).absolute()
    lk = read_lock(root)
    if lk and focus_other(lk):
        return "focused"
    cur = resolve_project(STATE.get("project"))
    if cur is not None and Path(cur).absolute() != root:
        spawn_window(root)
        return "spawned"
    acquire(root)
    return "here"


def set_title(p) -> None:
    w = WIN["window"]
    if w is not None:
        try:
            w.title = f"Ti64 EBSD/BSE — {p.info().name}"
        except Exception:  # noqa: BLE001
            pass


def bring_to_front() -> None:
    w = WIN["window"]
    if w is None:
        return
    try:
        w.restore()
        w.show()
        w.on_top = True
        time.sleep(0.2)
        w.on_top = False
    except Exception:  # noqa: BLE001
        log.exception("bring to front")


# ---- server + window ------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(port: int, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_desktop(project: str | None = None, port: int | None = None) -> None:
    from .app import ICON, TITLE, setup_logging  # noqa: PLC0415

    log_file = setup_logging()
    if sys.stdout is None:                       # pythonw: no console
        sys.stdout = open(log_file.with_name("ti64-gui.stdout.log"), "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout

    def excepthook(t, v, tb):
        log.error("unhandled error:\n%s", "".join(traceback.format_exception(t, v, tb)))
        fatal(f"Ti64 EBSD/BSE stopped because of an error:\n\n{v}", log_file)
    sys.excepthook = excepthook
    try:
        import webview  # noqa: PLC0415
    except ImportError:
        fatal("pywebview is not installed in this environment. Install it with\n    pip install pywebview\n"
              "or start the app in the browser:  ti64 gui --browser", log_file)
        return
    if os.name == "nt" and webview2_version() is None:
        fatal(WEBVIEW2_MISSING, log_file)
        return
    from ..project import resolve_project  # noqa: PLC0415

    root = resolve_project(project)
    if root is not None and (root / "project.json").is_file():
        lk = read_lock(root)
        if lk and focus_other(lk):
            log.info("project %s already open in pid %s; brought to front", root, lk["pid"])
            return
    import uvicorn  # noqa: PLC0415
    from fastapi import FastAPI, Response  # noqa: PLC0415
    from nicegui import ui  # noqa: PLC0415

    from . import dialogs, shell  # noqa: PLC0415
    from .app import build  # noqa: PLC0415
    from .state import STATE, open_project  # noqa: PLC0415

    port = port or free_port()
    WIN["port"] = port
    api = FastAPI()

    @api.get("/api/ti64/focus")
    def _focus() -> Response:
        threading.Thread(target=bring_to_front, daemon=True).start()
        return Response(status_code=200)

    build()
    ui.run_with(api, title=TITLE, favicon=str(ICON) if ICON.is_file() else "🔬", show_welcome_message=False,
                reconnect_timeout=30.0)
    shell.DESKTOP.update(request_open=request_open, set_title=set_title, release=release)
    if root is not None and (root / "project.json").is_file():
        try:
            open_project(root)
            acquire(root)
        except Exception as e:  # noqa: BLE001
            log.exception("open %s", root)
            message_box(f"Could not open the project {root}:\n{e}")
            STATE["project"] = None
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    th = threading.Thread(target=server.run, name="ti64-server", daemon=True)
    th.start()
    if not _wait(port):
        fatal("The app's local server did not start (see the log).", log_file)
        return
    window = webview.create_window(TITLE, f"http://127.0.0.1:{port}/", width=1366, height=820, min_size=(1024, 640),
                                   text_select=True)
    WIN["window"] = window
    dialogs.WINDOW["w"] = window
    if STATE.get("project"):
        try:
            from ..project import Project  # noqa: PLC0415

            set_title(Project.open(resolve_project(STATE["project"])))
        except Exception:  # noqa: BLE001
            pass
    window.events.closed += lambda: release()
    log.info("window on port %s, project %s", port, STATE.get("project"))
    try:
        webview.start(icon=str(ICON) if ICON.is_file() else None, private_mode=False,
                      storage_path=str(log_file.parent.parent / "webview"))
    finally:
        release()
        server.should_exit = True
        th.join(timeout=5)


def main() -> None:
    """gui-scripts entry point (ti64-gui.exe)."""
    from .app import main as app_main  # noqa: PLC0415

    app_main()
