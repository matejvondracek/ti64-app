"""Screenshots of the screens changed by the fourth round of fixes after the phase 6.6 user test: map sheet 'Phases'
(APEX names, ambiguous mapping) and 'PRIAS' sections, Add file with PRIAS exports, PRIAS in the EBSD 'Maps' list,
.up2 metadata (scan ID, paired map, padding patterns), View tab .up2 overview as hexagonal cells with the paired map,
Help menu / Save report for Claude / Feedback without e-mail / Settings, Stitch tab with one Export button.

Usage:  python scripts/gui_screenshots_fix4.py [--out projects/_screens_fix4] [--port 8098] [--headed] [--no-stitch]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_screenshots import DATA, REPO, Run, choose, mark, wait_idle  # noqa: E402

JU04, JU06 = REPO / "data" / "JU04", REPO / "data" / "JU06"


def prepare(work: Path) -> str:
    """Project 'fix4_demo' in work/projects with the files linked (no GUI)."""
    env_backup = dict(os.environ)
    os.environ["TI64_PROJECTS_DIR"] = str(work / "projects")
    os.environ["TI64_PREFS"] = str(work / "prefs" / "preferences.json")
    sys.path.insert(0, str(REPO / "python"))
    from ti64app.modules import up2_overview as uo
    from ti64app.project import Project

    p = Project.create("fix4_demo", base=work / "projects")
    # an .ang with two hexagonal phases: the symmetry cannot tell alpha from beta (map sheet asks)
    amb = work / "two_hex_phases.ang"
    src = (JU06 / "JU06_tensile_mid.ang").read_text(encoding="latin-1").splitlines()
    i = next(k for k, ln in enumerate(src) if ln.startswith("# GRID"))
    extra = ["# Phase 2", "# MaterialName  \tTi3Al (alpha2)", "# Formula     \tTi3Al", "# Info\t\t",
             "# Symmetry              62", "# LatticeConstants      5.780 5.780 4.640  90.000  90.000 120.000",
             "# NumberFamilies        0"]
    amb.write_text("\n".join(src[:i] + extra + src[i:]) + "\n", encoding="latin-1")
    files = [JU04 / "JU04_area1.ang", JU06 / "JU06_tensile.ang", amb, next(JU04.glob("*Area 1*.up2"))]
    stitch = sorted((DATA / "stitch").glob("*.bmp"))[:4] if (DATA / "stitch").is_dir() else []
    p.add_files([str(f) for f in files + stitch])
    up2f = next(JU04.glob("*Area 1*.up2"))
    t = time.time()
    uo.compute(up2f, p.cache_dir, 2)
    print(f"  overview pre-computed in {time.time() - t:.0f} s")
    os.environ.clear()
    os.environ.update(env_backup)
    return p.root.name


def fid(page, name_part: str):
    return page.get_by_text(name_part, exact=True).first


def flow(r: Run, page, base: str, pname: str, work: Path, do_stitch: bool) -> None:
    def open_project():
        page.goto(base + "/")
        wait_idle(page, 800)
        mark(page, "home_open").click()
        mark(page, f"recent_{pname}").click()
        wait_idle(page, 1500)
        page.goto(base + "/files")
        wait_idle(page, 1200)
    r.step("open project", open_project)

    def files_sheets():
        fid(page, "JU04_area1.ang").click()
        wait_idle(page, 1500)
        r.shot("files_ju04_area1_map_sheet")
        mark(page, "ms_phases").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_map_sheet_phases_by_symmetry_and_prias")
        fid(page, "two_hex_phases.ang").click()
        wait_idle(page, 1500)
        mark(page, "ms_phases").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_map_sheet_phases_ambiguous")
        fid(page, "MVH1_JU04_Area 1_OIM Map 1.up2").click()
        wait_idle(page, 1500)
        mark(page, "file_meta").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_up2_scan_id_paired_map_padding")
    r.step("Files: map sheets", files_sheets)

    def add_with_prias():
        r.queue([str(JU06 / "JU06_compression.ang"), str(JU06 / "JU06_compression_prias.txt")])
        mark(page, "add_file").click()
        page.wait_for_timeout(1500)
        r.shot("files_add_file_with_prias_export")
        wait_idle(page, 800)
    r.step("Files: Add file with PRIAS", add_with_prias)

    def ebsd_prias():
        fid(page, "JU06_tensile.ang").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=300_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("ebsd_ju06_tensile_raw_single_phase_ipf")
        mark(page, "viewer_kind").click()
        page.wait_for_timeout(600)
        r.shot("ebsd_maps_list_with_prias_top_strip")
        page.keyboard.press("Escape")
        choose(page, "viewer_kind", "PRIAS top strip")
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("ebsd_prias_top_strip_map")
    r.step("EBSD: PRIAS map", ebsd_prias)

    def ebsd_two_phase():
        page.goto(base + "/files")
        wait_idle(page, 1000)
        fid(page, "JU04_area1.ang").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=300_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        choose(page, "viewer_kind", "phase")
        wait_idle(page, 1500)
        page.wait_for_timeout(2000)
        r.shot("ebsd_ju04_area1_apex_names_phase_map")
    r.step("EBSD: APEX two-phase map", ebsd_two_phase)

    def ebsd_ambiguous():
        page.goto(base + "/files")
        wait_idle(page, 1000)
        fid(page, "two_hex_phases.ang").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        page.wait_for_timeout(6000)
        wait_idle(page, 1500)
        r.shot("ebsd_ambiguous_phases_open_map_sheet")
    r.step("EBSD: ambiguous phases", ebsd_ambiguous)

    def view_up2():
        page.goto(base + "/files")
        wait_idle(page, 1000)
        fid(page, "MVH1_JU04_Area 1_OIM Map 1.up2").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        mark(page, "up2_map").wait_for(timeout=300_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("view_up2_overview_paired_map_comparison")
        mark(page, "up2_channel").click()
        page.wait_for_timeout(600)
        r.shot("view_up2_map_list_with_paired_ang_channels")
        page.keyboard.press("Escape")
        box = mark(page, "up2_map").bounding_box()
        cx, cy = box["x"] + box["width"] * 0.47, box["y"] + box["height"] * 0.55     # inside the map image
        page.mouse.move(cx, cy)
        for _ in range(5):
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(350)
        page.wait_for_timeout(2500)
        r.shot("view_up2_zoomed_hexagonal_cells")
        page.mouse.click(cx, cy)
        page.wait_for_timeout(2500)
        r.shot("view_up2_click_pattern_with_paired_values")
        choose(page, "up2_channel", "IQ of the paired")
        page.wait_for_timeout(3000)
        r.shot("view_up2_zoomed_paired_ang_iq_same_cells")
    r.step("View: .up2 hex overview + paired map", view_up2)

    def help_report():
        mark(page, "help_btn").click()
        page.mouse.move(5, 400)
        page.wait_for_timeout(600)
        r.shot("help_menu")
        mark(page, "help_report").click()
        mark(page, "report_dialog").wait_for()
        page.locator('[data-testid="rep_text"] textarea, textarea[data-testid="rep_text"]').first \
            .fill("The .up2 overview of Area 1 had a diagonal seam; check that it is gone.")
        page.wait_for_timeout(400)
        r.shot("save_report_for_claude_dialog")
        mark(page, "rep_save").click()
        page.wait_for_timeout(2500)
        r.shot("report_saved_notification")
        page.keyboard.press("Control+Shift+R")
        page.wait_for_timeout(1500)
        r.shot("ctrl_shift_r_opens_report_dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        mark(page, "feedback_btn").click()
        mark(page, "feedback_dialog").wait_for()
        page.wait_for_timeout(500)
        r.shot("feedback_dialog_github_and_report_no_email")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        mark(page, "settings_gear").click()
        mark(page, "settings_feedback").scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        r.shot("settings_feedback_without_email")
        page.keyboard.press("Escape")
    r.step("Help menu, report for Claude, feedback", help_report)

    if do_stitch:
        def stitch():
            page.goto(base + "/stitch")
            mark(page, "st_run").wait_for(timeout=180_000)
            wait_idle(page, 2000)
            mark(page, "st_run").click()
            mark(page, "st_summary_BSE").wait_for(timeout=900_000)
            wait_idle(page, 2000)
            page.wait_for_timeout(1500)
            r.shot("stitch_result_one_export_button")
        r.step("Stitch: one Export button", stitch)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_fix4"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix4"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-stitch", action="store_true")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    for d in (out, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    if not (JU04 / "JU04_area1.ang").is_file():
        print("data/JU04 missing")
        return 1
    pname = prepare(work)
    mock = work / "dialogs.json"
    mock.write_text('{"queue": []}', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.update(PYTHONPATH=str(REPO / "python"), TI64_MOCK_DIALOGS=str(mock),
               TI64_PROJECTS_DIR=str(work / "projects"), TI64_PREFS=str(work / "prefs" / "preferences.json"))
    log = open(work / "server.log", "w", encoding="utf-8")  # noqa: SIM115
    srv = subprocess.Popen([sys.executable, "-m", "ti64app.gui", "--browser", "--no-show", "--port", str(a.port)],
                           cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{a.port}"
    r = None
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base, timeout=1)  # noqa: S310
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=not a.headed)
            page = br.new_page(viewport={"width": 1366, "height": 768})
            page.set_default_timeout(60_000)
            r = Run(page, out, mock)
            flow(r, page, base, pname, work, not a.no_stitch)
            r.no_hscroll("last page")
            br.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
    if r is None:
        return 1
    rep = sorted((REPO / "reports").glob("*.md"))
    print("newest report:", rep[-1] if rep else None)
    print("\n".join(["failed steps:"] + r.failed) if r.failed else "all steps ok")
    print("\n".join(x for x in r.logs if "pageerror" in x))
    (out / "summary.json").write_text(json.dumps({"failed": r.failed, "logs": r.logs[-50:]}, indent=1))
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
