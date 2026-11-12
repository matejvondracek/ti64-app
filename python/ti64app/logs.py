"""Log files of the app: %APPDATA%/ti64app/logs/ (settings.logs_dir; outside the repository, never committed).

  ti64-gui.log          GUI (gui/app.setup_logging)
  ti64-cli.log          `ti64 run / replay / stitch ...`
  ti64-mcp.log          MCP server (stdout is the MCP wire, so nothing may be printed there)
  matlab-engine-<pid>.log   MATLAB command-window output of the engine started by Python process <pid> (-logfile)
  matlab_crash_dump.*   MATLAB crash dumps of that engine (MATLAB_LOG_DIR points here)
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .settings import logs_dir


def setup_logging(name: str) -> Path:
    """Add a rotating file handler logs/ti64-<name>.log (3 x 2 MB) to the root logger once; returns its path."""
    d = logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"ti64-{name}.log"
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == f for h in root.handlers):
        h = RotatingFileHandler(f, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
    from .breadcrumbs import install  # noqa: PLC0415

    install()
    return f


def prune(pattern: str, keep: int = 10) -> None:
    """Delete all but the `keep` newest files in the logs folder matching `pattern`."""
    files = sorted(logs_dir().glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass
