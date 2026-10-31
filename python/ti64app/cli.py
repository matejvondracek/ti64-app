"""Command-line interface: ti64 backends | gui | mcp | replay <project>."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ti64", description="Ti64 EBSD/BSE analysis")
    ap.add_argument("--version", action="version", version=f"ti64app {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("backends", help="show which analysis backends are available")
    g = sub.add_parser("gui", help="start the GUI on http://127.0.0.1:<port>")
    g.add_argument("--port", type=int, default=8064)
    g.add_argument("--project")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    r = sub.add_parser("replay", help="replay a project's recipe and compare outputs")
    r.add_argument("project")
    r.add_argument("--backend", choices=["pymtex", "matlab"])
    a = ap.parse_args(argv)
    if a.cmd == "backends":
        from .backends import backend_status  # noqa: PLC0415

        print(json.dumps({k: (v or "available") for k, v in backend_status().items()}, indent=1))
    elif a.cmd == "gui":
        from .gui.app import main as gui_main  # noqa: PLC0415

        gui_main(port=a.port, project=a.project)
    elif a.cmd == "mcp":
        from .mcp_server import main as mcp_main  # noqa: PLC0415

        mcp_main()
    elif a.cmd == "replay":
        from . import service  # noqa: PLC0415
        from .project import Project, projects_dir  # noqa: PLC0415

        rep = service.replay(Project.open(projects_dir() / a.project), backend=a.backend)
        print(json.dumps({"ok": rep.ok, **rep.model_dump(mode="json")}, indent=1))
        return 0 if rep.ok else 1
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
