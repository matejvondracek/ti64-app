"""Screenshots of the screens changed by the fifth round of fixes after the phase 6.6 user test: Files tab tree
(indentation, indexing tag, re-indexing question, right-click menu, map sheet field), tooltips only on hover,
EBSD tab Load / Cleanup sections (sub-panels, default cleanup, tuning replaces, void-fill report, not-indexed
highlight, grain dilation), status badges, probe with file / app Euler angles, OIM axes in 'Maps and IPF key',
View tab .up2 layout (Pattern panel full height, status line ends at it).

Usage:  python scripts/gui_screenshots_fix5.py [--out projects/_screens_fix5] [--port 8099] [--headed]
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

JU04 = REPO / "data" / "JU04"


def prepare(work: Path) -> str:
    env_backup = dict(os.environ)
    os.environ["TI64_PROJECTS_DIR"] = str(work / "projects")
    os.environ["TI64_PREFS"] = str(work / "prefs" / "preferences.json")
    sys.path.insert(0, str(REPO / "python"))
    from ti64app.modules import up2_overview as uo
    from ti64app.project import Project

    p = Project.create("fix5_demo", base=work / "projects")
    rescan = next((DATA / "crops").glob("*JU02*Rescan*crop300.ang"))
    sem = sorted(JU04.glob("94[45]*_MVH1_JU04_*.bmp"))[:6]
    up2f = next(JU04.glob("*Area 1*.up2"))
    files = [JU04 / "JU04_area1.ang", JU04 / "JU04_area4.ang", up2f, *sem]
    p.add_files([str(f) for f in files])
    p.add_files([str(rescan)])
    from ti64app.sample import SampleInfo

    p.save_sample_info(SampleInfo(id="JU04"))
    p.save_sample_info(SampleInfo(id="JU02"))
    p.assign([m.id for m in p.files() if "JU04" in m.file or "MVH1" in m.file], "JU04")
    p.assign([m.id for m in p.files() if "JU02" in m.file], "JU02")
    uo.compute(up2f, p.cache_dir, 4)
    os.environ.clear()
    os.environ.update(env_backup)
    return p.root.name


def fid(page, name_part: str):
    return page.get_by_text(name_part, exact=True).first


def flow(r: Run, page, base: str, pname: str) -> None:
    def open_project():
        page.goto(base + "/")
        wait_idle(page, 800)
        mark(page, "home_open").click()
        mark(page, f"recent_{pname}").click()
        wait_idle(page, 1500)
        page.goto(base + "/files")
        wait_idle(page, 1200)
        page.wait_for_timeout(1200)
    r.step("open project", open_project)

    def files_question():
        mark(page, "indexing_dialog").wait_for(timeout=20_000)
        r.shot("files_question_reindexed_spherical")
        mark(page, "indexing_yes").click()
        page.wait_for_timeout(1200)
        r.shot("files_tree_indented_groups_and_spherical_tag")
    r.step("Files: indexing question", files_question)

    def files_menu():
        fid(page, "JU04_area1.ang").click(button="right")
        page.wait_for_timeout(700)
        r.shot("files_right_click_indexing_method")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        fid(page, "JU04_area1.ang").click()
        wait_idle(page, 1000)
        mark(page, "ms_indexing").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_map_sheet_indexing_method_field")
    r.step("Files: menu + map sheet", files_menu)

    def tooltips():
        page.mouse.move(400, 600)
        page.wait_for_timeout(300)
        r.shot("tooltips_none_without_hover")
        mark(page, "feedback_btn").hover()
        page.wait_for_timeout(900)
        r.shot("tooltip_feedback_on_hover")
        page.mouse.move(400, 600)
        page.wait_for_timeout(600)
        r.shot("tooltip_feedback_gone_after_mouse_leave")
        mark(page, "feedback_btn").click()
        mark(page, "feedback_dialog").wait_for()
        page.wait_for_timeout(800)
        r.shot("feedback_dialog_no_tooltip_left_on_screen")   # window capture itself: desktop mode only
        mark(page, "feedback_dialog").get_by_role("button", name="Cancel").click()
        page.wait_for_timeout(500)
    r.step("Tooltips", tooltips)

    def ebsd_open():
        fid(page, "JU04_area1.ang").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=300_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        mark(page, "sec_cleanup").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_load_and_cleanup_sections_no_badges_yet")
    r.step("EBSD: open JU04_area1", ebsd_open)

    def default_cleanup():
        mark(page, "add_default_cleanup").click()
        page.wait_for_timeout(1500)
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("ebsd_default_cleanup_three_steps_badges")
        mark(page, "sec_clean_void_fill").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_void_fill_report_left_large_voids")
        choose(page, "viewer_kind", "phase")
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        mark(page, "highlight_ni").click()
        wait_idle(page, 1500)
        page.wait_for_timeout(2500)
        r.shot("ebsd_highlight_not_indexed_magenta")
    r.step("EBSD: default cleanup, void report, highlight", default_cleanup)

    def tuning():
        num = page.locator('[data-testid="clean_void_fill_max_void_px"] input, input[data-testid="clean_void_fill_max_void_px"]').first
        num.fill("12")
        num.press("Tab")
        page.wait_for_timeout(800)
        r.shot("ebsd_badge_changed_not_applied")
        mark(page, "run_clean_void_fill").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("ebsd_tuning_replaced_void_fill_step")
    r.step("EBSD: tuning replaces", tuning)

    def dilation():
        mark(page, "sec_clean_grain_dilation").click()
        page.wait_for_timeout(600)
        mark(page, "run_clean_grain_dilation").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("ebsd_grain_dilation_per_iteration")
    r.step("EBSD: grain dilation", dilation)

    def gs_badge():
        mark(page, "sec_grainsize").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_grain_size_never_used_no_badge")
    r.step("EBSD: grain size badge", gs_badge)

    def probe():
        page.goto(base + "/files")
        wait_idle(page, 1000)
        fid(page, "JU04_area4.ang").click()
        wait_idle(page, 800)
        page.wait_for_timeout(12000)            # the 2.6 M-point map sheet (PRIAS check) takes a few seconds
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=600_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(2000)
        choose(page, "viewer_tool", "probe")
        page.wait_for_timeout(500)
        page.evaluate("getElement(window.ti64Viewer).$emit('probe', {xy: [[61.0, 14.9]]})")
        page.wait_for_timeout(2500)
        r.shot("ebsd_probe_file_and_app_euler_ju04_area4")
        mark(page, "sec_ipf").click()
        page.wait_for_timeout(800)
        mark(page, "ipf_oim_axes").hover()
        page.wait_for_timeout(1000)
        r.shot("ebsd_maps_panel_oim_axes")
        mark(page, "sec_load").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_load_section_frame_info")
    r.step("EBSD: probe + OIM axes", probe)

    def view_up2():
        page.goto(base + "/files")
        wait_idle(page, 1000)
        fid(page, "MVH1_JU04_Area 1_OIM Map 1.up2").click()
        wait_idle(page, 800)
        mark(page, "open_tool").click()
        mark(page, "up2_map").wait_for(timeout=300_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        box = mark(page, "up2_map").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(800)
        r.shot("view_up2_pattern_panel_full_height_status_ends_at_panel")
    r.step("View: .up2 layout", view_up2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_fix5"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix5"))
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    for d in (out, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
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
            page = br.new_page(viewport={"width": 1920, "height": 1032})
            page.set_default_timeout(60_000)
            r = Run(page, out, mock)
            flow(r, page, base, pname)
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
    print("\n".join(["failed steps:"] + r.failed) if r.failed else "all steps ok")
    print("\n".join(x for x in r.logs if "pageerror" in x))
    (out / "summary.json").write_text(json.dumps({"failed": r.failed, "logs": r.logs[-50:]}, indent=1))
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
