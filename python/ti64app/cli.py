"""Command-line interface for batch runs."""
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
    a = ap.parse_args(argv)
    if a.cmd == "backends":
        from .backends import backend_status  # noqa: PLC0415

        print(json.dumps({k: (v or "available") for k, v in backend_status().items()}, indent=1))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
