"""Command-line interface: ti64 backends | gui | mcp | run <recipe.json> | replay <project> | ops."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__


def _default_project_name(recipe: Path) -> str:
    stem = recipe.parent.name if recipe.name == "recipe.json" else recipe.stem
    return f"{stem}_run_{datetime.now():%Y%m%d_%H%M%S}"


def cmd_run(a) -> int:
    """ti64 run recipe.json: run every step of a recipe into a project (new by default) and compare outputs."""
    from . import service  # noqa: PLC0415
    from .project import Project, projects_dir  # noqa: PLC0415
    from .settings import load_settings  # noqa: PLC0415

    f = Path(a.recipe)
    steps = service.load_recipe_file(f)
    name = a.project or _default_project_name(f)
    root = projects_dir() / name
    p = Project.open(root) if (root / "project.json").is_file() else Project.create(
        name, backend=a.backend or load_settings().backend)
    recorded = f.parent if (f.parent / "project.json").is_file() else None

    def progress(k: int, n: int, s) -> None:
        be = (a.backend or s.backend or p.info().backend) if service.OPS[s.op].uses_backend else "-"
        print(f"[{k + 1}/{n}] {s.op} {s.params.get('map_id', '')} ({be}) ...", file=sys.stderr, flush=True)

    rep = service.run_recipe(p, steps, backend=a.backend, include_stale=a.include_stale, until=a.until,
                             recorded_root=recorded, compare=not a.no_compare, on_step=progress)
    out = {"ok": rep.ok, **rep.model_dump(mode="json")}
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for s in rep.steps:
            print(f"{s.step_id}  {s.op:<22} {s.map_id or '':<16} {s.backend or '-':<7} {s.seconds:8.1f} s")
        if rep.skipped:
            print(f"skipped (stale): {', '.join(rep.skipped)}")
        for c in rep.input_changes:
            print(f"INPUT {c}")
        bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance", "not_compared")]
        if rep.checks:
            print(f"outputs: {len(rep.checks) - len(bad)}/{len(rep.checks)} reproduced")
        for c in bad:
            print(f"  {c.step_id} {c.op} {c.output}: {c.status} {c.detail}")
        print(f"project: {rep.project_root}")
    return 0 if rep.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ti64", description="Ti64 EBSD/BSE analysis")
    ap.add_argument("--version", action="version", version=f"ti64app {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("backends", help="show which analysis backends are available")
    sub.add_parser("ops", help="list the service operations (same as the MCP tools)")
    g = sub.add_parser("gui", help="start the GUI on http://127.0.0.1:<port>")
    g.add_argument("--port", type=int, default=8064)
    g.add_argument("--project")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    r = sub.add_parser("run", help="run a recipe file (a project's recipe.json or a list of steps) into a project")
    r.add_argument("recipe", help="recipe.json")
    r.add_argument("--project", help="target project (default: new <name>_run_<time>); created if missing")
    r.add_argument("--backend", choices=["pymtex", "matlab"], help="run every backend step on this backend")
    r.add_argument("--include-stale", action="store_true", help="also run steps marked stale")
    r.add_argument("--until", help="stop after this step id")
    r.add_argument("--no-compare", action="store_true", help="do not compare with recorded outputs")
    r.add_argument("--json", action="store_true", help="print the full report as JSON")
    rp = sub.add_parser("replay", help="replay a project's recipe in a scratch copy and compare outputs")
    rp.add_argument("project")
    rp.add_argument("--backend", choices=["pymtex", "matlab"])
    a = ap.parse_args(argv)
    if a.cmd == "backends":
        from .backends import backend_status  # noqa: PLC0415

        print(json.dumps({k: (v or "available") for k, v in backend_status().items()}, indent=1))
    elif a.cmd == "ops":
        from . import service  # noqa: PLC0415

        for n, spec in sorted(service.OPS.items()):
            print(f"{n:<22} {'backend' if spec.uses_backend else '-':<8} {spec.doc.splitlines()[0] if spec.doc else ''}")
    elif a.cmd == "gui":
        from .gui.app import main as gui_main  # noqa: PLC0415

        gui_main(port=a.port, project=a.project)
    elif a.cmd == "mcp":
        from .mcp_server import main as mcp_main  # noqa: PLC0415

        mcp_main()
    elif a.cmd == "run":
        return cmd_run(a)
    elif a.cmd == "replay":
        from . import service  # noqa: PLC0415
        from .project import Project, projects_dir  # noqa: PLC0415

        rep = service.replay(Project.open(projects_dir() / a.project), backend=a.backend)
        print(json.dumps({"ok": rep.ok, "replay_root": rep.replay_root, **rep.model_dump(mode="json")}, indent=1))
        return 0 if rep.ok else 1
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
