"""Memory use for the top bar (fix 3.1) and the system part of a feedback report (fix 3.2)."""
from __future__ import annotations

import os
import platform
import sys

import psutil

WARN_FRACTION = 0.80          # top bar turns orange above 80 % of the PC's RAM in use


def _rss(pid: int | None) -> int:
    if not pid:
        return 0
    try:
        return psutil.Process(pid).memory_info().rss
    except (psutil.Error, OSError):
        return 0


def matlab_pid() -> int | None:
    """PID of the MATLAB engine process started by this app, if it is running (never the user's own MATLAB)."""
    try:
        from .backends import _instances  # noqa: PLC0415  (never create the backend just to ask)

        be = _instances.get("matlab")
    except Exception:  # noqa: BLE001
        return None
    if be is None or not getattr(be, "running", False):
        return None
    return getattr(be, "_pid", None)


def memory_status() -> dict:
    """Bytes used by the app (this Python process + the MATLAB engine when it runs) and by the whole PC."""
    py = _rss(os.getpid())
    mpid = matlab_pid()
    ml = _rss(mpid)
    vm = psutil.virtual_memory()
    used = vm.total - vm.available
    return {"python": py, "matlab": ml, "matlab_running": mpid is not None, "app": py + ml,
            "pc_used": used, "pc_total": vm.total, "pc_fraction": used / vm.total if vm.total else 0.0,
            "warn": vm.total > 0 and used / vm.total > WARN_FRACTION}


def gb(n: float) -> str:
    return f"{n / 2**30:.1f} GB"


def memory_text(m: dict) -> tuple[str, str]:
    """(short top-bar text, tooltip)."""
    short = f"App {gb(m['app'])} · PC {gb(m['pc_used'])} / {gb(m['pc_total'])}"
    tip = (f"Memory of this app: Python {gb(m['python'])}"
           + (f" + MATLAB engine {gb(m['matlab'])}" if m["matlab_running"] else " (MATLAB engine not running)")
           + f" = {gb(m['app'])}.\nWhole PC: {gb(m['pc_used'])} of {gb(m['pc_total'])} in use "
             f"({100 * m['pc_fraction']:.0f} %). Turns orange above {100 * WARN_FRACTION:.0f} %: close other "
             "programs or maps before a large step. Refreshed every 3 s.")
    return short, tip


def system_summary() -> dict:
    """Plain facts for a bug report (no user names or paths)."""
    vm = psutil.virtual_memory()
    return {"os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python": sys.version.split()[0], "ram": gb(vm.total), "cpu_count": os.cpu_count()}
