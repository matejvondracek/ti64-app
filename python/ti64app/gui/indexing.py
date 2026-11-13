"""Indexing method label of an EBSD map (phase 6.6 fix 5, item 15): 'Hough' (microscope default) or 'Spherical'
(re-indexed in OIM Analysis). Asked once per file when the file name contains 'rescan' or the phases have the older
OIM names 'Ti (alpha)' / 'Ti (beta)'; the answer (also 'Not sure') is remembered in the map sheet (files.json)."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ..project import Project
from ..sample import INDEXING_LABELS, needs_indexing_question

OPTIONS = {"": "no label", "hough": "Hough (microscope default)", "spherical": "Spherical (re-indexed in OIM)"}
TAG_TIP = {"hough": "Indexed with the Hough transform (the microscope's default)",
           "spherical": "Re-indexed with spherical indexing in OIM Analysis"}


def tag(m) -> None:
    """Small tag next to a file name / in the map info; nothing without a label."""
    if getattr(m, "indexing", None):
        ui.label(INDEXING_LABELS[m.indexing]).classes("ti-tag").tooltip(TAG_TIP[m.indexing]) \
            .mark(f"indexing_tag_{m.id}")


def set_label(p: Project, map_id: str, value: str | None) -> None:
    p.update_file(map_id, indexing=value or None, indexing_asked=True)


def ask_pending(p: Project, on_done=None, only: list[str] | None = None) -> bool:
    """Ask the question for the first file of the project that needs it (then the next, one dialog at a time).
    only = these map ids. Returns True if a dialog was opened."""
    todo = [(m, r) for m in p.files() if (only is None or m.id in only) and (r := needs_indexing_question(m))]
    if not todo:
        return False
    m, reason = todo[0]
    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-[34rem] max-w-full gap-3") \
            .mark("indexing_dialog"):
        ui.label("Was this map re-indexed with spherical indexing?").classes("text-lg font-semibold")
        ui.label(f"{m.sample_id or 'Unassigned'} / {Path(m.file).name}").classes("font-mono text-sm break-all")
        ui.label(f"It looks re-indexed in OIM Analysis: {reason}. The answer labels the map 'Spherical' or 'Hough' "
                 "(Files tab, map info, reports, exports). You can change or clear it later on the Files tab (file "
                 "details, or right-click the file).").classes("text-sm ti-muted")

        def answer(v: str | None):
            set_label(p, m.id, v)
            dlg.close()
            if on_done:
                on_done()
            if len(todo) > 1:
                ask_pending(p, on_done, only)
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Not sure", on_click=lambda: answer(None)).props("flat").mark("indexing_unsure") \
                .tooltip("No label (you will not be asked again for this file)")
            ui.button("No (Hough)", on_click=lambda: answer("hough")).props("outline").mark("indexing_no")
            ui.button("Yes (Spherical)", on_click=lambda: answer("spherical")).mark("indexing_yes")
    dlg.open()
    return True
