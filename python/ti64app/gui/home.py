"""Home page (no project open): top bar with New Project / Open Project, below a short title page."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ..project import recent_projects
from .shell import header, new_project_dialog, request_open

FEATURES = [
    ("grain", "EBSD maps and cleanup",
     "Load TSL/OIM .ang maps (hexagonal or square grid), raw or OIM-cleaned; CI threshold, noise and spike removal. "
     "IPF, phase, CI and IQ maps with the IPF key of your choice."),
    ("blur_on", "KAM / GND",
     "Local misorientation (KAM, GROD, GOS) and geometrically necessary dislocation density maps."),
    ("hub", "Prior-β reconstruction",
     "Parent β grains from the α laths with the variant-graph method and the Burgers orientation relationship, "
     "with fit diagnostics and a parameter sweep."),
    ("straighten", "Grain size",
     "α lath thickness, colony and prior-β grain size by ISO 13067 / ASTM E2627 rules; tables and histograms, "
     "checked against OIM."),
    ("view_module", "Stitching",
     "Stitch overlapping JEOL BSE / SE images into one mosaic with a new info strip and scale bar."),
    ("timeline", "Crack analysis",
     "Trace fatigue cracks on the map and relate them to the microstructure (coming in phase 8)."),
    ("compress", "4-point-bending stress mapping",
     "Stress at each map position from the specimen geometry and load (tension / middle / compression)."),
]


def home_page() -> None:
    header(None)
    with ui.column().classes("ti-home w-full max-w-5xl mx-auto px-6 py-8 gap-6"):
        with ui.column().classes("gap-2"):
            ui.html("<h1>Ti64 EBSD/BSE</h1>").mark("home_title")
            ui.label("Microstructure analysis of electron-beam-melted Ti-6Al-4V (α + retained β) from EBSD maps "
                     "and SEM images. Every step is recorded in the project's recipe, so results can be undone, "
                     "replayed and compared between the two analysis engines (MTEX for Python or MATLAB + MTEX)."
                     ).classes("text-base max-w-3xl")
        with ui.grid().classes("w-full gap-3").style("grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))"):
            for icon, title, text in FEATURES:
                with ui.row().classes("ti-card p-4 no-wrap items-start gap-3"):
                    ui.icon(icon, size="28px").classes("text-primary mt-1")
                    with ui.column().classes("gap-1"):
                        ui.label(title).classes("font-semibold")
                        ui.label(text).classes("text-sm ti-muted")
        with ui.row().classes("gap-3 items-center"):
            ui.button("New Project", icon="create_new_folder", on_click=new_project_dialog).props("size=lg")
            rec = recent_projects(5)
            if rec:
                ui.label("or open a recent project:").classes("ti-muted")
                for r in rec:
                    ui.button(Path(r).name, icon="history", on_click=lambda r=r: request_open(r)) \
                        .props("outline").tooltip(r)
        ui.label("Data files are linked, never copied. Projects save every change immediately.") \
            .classes("text-xs ti-muted")
