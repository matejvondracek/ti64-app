"""NiceGUI front end (phase 6.6 layout, docs/gui_mockups/): home page (/) when no project is open, else the tabs
Files (/files), View (/view: images, mosaics, .up2 patterns), EBSD (/ebsd), Crack (/crack, placeholder until
phase 8) and Stitch (/stitch); Settings is a dialog
behind the gear icon. All actions go through ti64app.service (recorded in recipe.json), the same as the MCP tools.

Desktop app: ti64-gui (gui/desktop.py, a pywebview window). Development / tests / Claude:
    python -m ti64app.gui --browser [--port 8064] [--project <root or name>]   -> http://127.0.0.1:8064
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Response
from nicegui import app as ng_app
from nicegui import ui

from ..sample import BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file
from ..settings import logs_dir
from .state import STATE, current_project  # noqa: F401  (tests set gui.STATE["project"])

ICON = Path(__file__).resolve().parents[3] / "docs" / "icon.ico"
TITLE = "Ti64 EBSD/BSE"


def setup_logging() -> Path:
    """Log file for the GUI (the desktop app has no console): %APPDATA%/ti64app/logs/ti64-gui.log."""
    d = logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / "ti64-gui.log"
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == f for h in root.handlers):
        h = RotatingFileHandler(f, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    from ..breadcrumbs import install  # noqa: PLC0415  (errors since start, for the report for Claude)

    install()
    return f


def crack_page() -> None:
    from .shell import header  # noqa: PLC0415

    p = header("Crack")
    if p is None:
        ui.navigate.to("/")
        return
    with ui.column().classes("w-full max-w-3xl mx-auto p-8 gap-3").mark("crack_placeholder"):
        ui.icon("timeline", size="48px").classes("text-primary")
        ui.label("Crack analysis").classes("text-2xl font-semibold")
        ui.label("Coming in phase 8: trace fatigue cracks on BSE / EBSD maps (crack-path annotations already work in "
                 "the EBSD tab), relate them to the microstructure and to the 4-point-bending stress.").classes("ti-muted")


def index_page() -> None:
    from .home import home_page  # noqa: PLC0415

    p = current_project()
    if p is not None:
        ui.navigate.to(p.gui_state().get("tab") or "/files")
        return
    home_page()


def build() -> None:
    from . import tiles  # noqa: F401, PLC0415  (registers the tile routes)
    from .ebsd_tab import ebsd_page  # noqa: PLC0415
    from .files_tab import files_page  # noqa: PLC0415
    from .stitch_page import stitch_page  # noqa: PLC0415
    from .view_tab import view_page  # noqa: PLC0415
    from .theme import setup_defaults  # noqa: PLC0415

    setup_defaults()
    ui.page("/", title=TITLE)(index_page)
    ui.page("/files", title=TITLE)(files_page)
    ui.page("/view", title=TITLE)(view_page)
    ui.page("/ebsd", title=TITLE)(ebsd_page)
    ui.page("/crack", title=TITLE)(crack_page)
    ui.page("/stitch", title=TITLE)(stitch_page)
    for old, new in (("/viewer", "/ebsd"), ("/maps", "/ebsd"), ("/recon", "/ebsd"), ("/grainsize", "/ebsd"),
                     ("/ops", "/ebsd"), ("/recipe", "/ebsd"), ("/compare", "/ebsd"), ("/sample", "/files"),
                     ("/settings", "/files")):
        ui.page(old)(lambda new=new: ui.navigate.to(new))     # phase 5 / 6 bookmarks

    @ng_app.post("/api/ti64/open_log")
    def _open_log() -> Response:
        f = logs_dir() / "ti64-gui.log"
        if f.is_file():
            os.startfile(str(f))          # noqa: S606
        return Response(status_code=204)

    @ng_app.post("/api/ti64/reveal")
    def _reveal(path: str) -> Response:
        import subprocess  # noqa: PLC0415

        if Path(path).exists():
            subprocess.Popen(["explorer", "/select,", str(Path(path))])  # noqa: S603, S607
        return Response(status_code=204)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="ti64-gui", description=f"{TITLE} desktop app")
    ap.add_argument("--browser", action="store_true", help="serve in the browser instead of a desktop window "
                                                           "(Playwright tests, Claude, development)")
    ap.add_argument("--port", type=int, default=None, help="port (default: 8064 in --browser mode, free port otherwise)")
    ap.add_argument("--project", help="open this project (root folder, or a name under projects/)")
    ap.add_argument("--no-show", action="store_true", help="--browser: do not open a browser tab")
    return ap.parse_known_args(argv)[0]


def main(port: int | None = None, project: str | None = None, browser: bool | None = None,
         argv: list[str] | None = None) -> None:
    """`python -m ti64app.gui` / `ti64 gui`: desktop window unless --browser (tests always use the browser mode)."""
    a = parse_args(argv if argv is not None else sys.argv[1:])
    port = port or a.port
    project = project or a.project
    testing = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("NICEGUI_SCREEN_TEST_PORT")
    if browser is None:
        browser = a.browser or bool(testing)
    if not browser:
        from .desktop import run_desktop  # noqa: PLC0415

        run_desktop(project=project, port=port)
        return
    setup_logging()
    STATE["project"] = project
    build()
    ui.run(host="127.0.0.1", port=port or 8064, title=TITLE, reload=False, show=not a.no_show and not testing,
           storage_secret=None, favicon=str(ICON) if ICON.is_file() else "🔬", show_welcome_message=not testing)


# ---- kept for the sample-sheet form tests and scripts (phase 5 API) ------------------------------------------
def sheet_from_form(v: dict) -> SampleSheet:
    """Build a SampleSheet from form values."""
    if v.get("bd_mode") == "vector":
        bd = BuildDirection(vector=(v.get("bd_x") or 0.0, v.get("bd_y") or 0.0, v.get("bd_z") or 0.0))
    else:
        if v.get("bd_angle") is None:
            raise ValueError("build direction is required")
        bd = BuildDirection(angle_deg=float(v["bd_angle"]))
    geo = [v.get(k) for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm")]
    bend = None
    if any(g is not None for g in geo):
        if any(g is None for g in geo):
            raise ValueError("4PB: enter outer span, inner span, width and thickness (or none)")
        bend = FourPointBending(outer_span_mm=geo[0], inner_span_mm=geo[1], width_mm=geo[2],
                                thickness_mm=geo[3], load_N=v.get("load_N"))
    pos_keys = ("origin_X_mm", "origin_Y_mm", "origin_Z_mm", "map_x_axis", "map_y_axis")
    pos = None
    if any(v.get(k) is not None for k in pos_keys):
        pos = MapPosition(**{k: v[k] for k in pos_keys if v.get(k) is not None})
    if not v.get("input_state"):
        raise ValueError("choose the input state (raw / OIM-cleaned)")
    return SampleSheet(map_id=v.get("map_id") or map_id_from_file(v["map_file"]),
                       specimen_id=(v.get("specimen_id") or "").strip(), map_file=v.get("map_file") or "",
                       input_state=v["input_state"], build_direction=bd, bending=bend, position=pos,
                       notes=v.get("notes") or "")
