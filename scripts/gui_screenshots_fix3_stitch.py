"""Screenshots of the View / Stitch screens changed by the third round of fixes (3.6 images open with the microscope
info bar and without the app's scale bar; 3.16 zoomable stitch result with tile borders and seams; 3.17 'Do not use
stage positions', no stage-axis warning). Playwright, browser mode; output projects/_screens_fix3/ (git-ignored).

Usage:  python scripts/gui_screenshots_fix3_stitch.py [--out projects/_screens_fix3] [--port 8097] [--headed]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_screenshots import REPO, Run, choose, mark, wait_idle  # noqa: E402

JU04 = REPO / "data" / "JU04" / "stich along crack path"


def setup(work: Path) -> str:
    """Project with the JU04 crack-path BSE images linked (Unassigned) and one .bmp selected for the View tab."""
    sys.path.insert(0, str(REPO / "python"))
    os.environ["TI64_PROJECTS_DIR"] = str(work / "projects")
    from ti64app.project import Project  # noqa: PLC0415

    p = Project.create("fix3_stitch", base=work / "projects")
    files = sorted(f for f in JU04.glob("*.bmp") if "BED" in f.name)
    linked = p.add_files(files)
    p.save_gui_state({"view_file": linked[0].id})
    return p.root.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_fix3"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix3_stitch"))
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*_stitch_*.png"):
        f.unlink()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if not JU04.is_dir():
        print(f"test data missing: {JU04}")
        return 1
    name = setup(work)
    mock = work / "dialogs.json"
    mock.write_text('{"queue": []}', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.update(PYTHONPATH=str(REPO / "python"), TI64_MOCK_DIALOGS=str(mock),
               TI64_PROJECTS_DIR=str(work / "projects"), TI64_PREFS=str(work / "prefs" / "preferences.json"))
    log = open(work / "server.log", "w", encoding="utf-8")  # noqa: SIM115
    srv = subprocess.Popen([sys.executable, "-m", "ti64app.gui", "--browser", "--no-show", "--port", str(a.port)],
                           cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{a.port}"
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
            r.n = 0

            def shot(n: str) -> None:
                r.shot(f"stitch_{n}")

            def open_project():
                page.goto(base + "/")
                mark(page, "home_open").click()
                page.wait_for_timeout(500)
                mark(page, f"recent_{name}").click()
                wait_idle(page, 1500)
            r.step("open the project", open_project)

            def view():
                page.goto(base + "/view")
                mark(page, "view_viewer").wait_for(timeout=60_000)
                wait_idle(page, 2500)
                shot("view_bmp_info_bar_on_app_scale_bar_off")
                mark(page, "view_databar").hover()
                page.wait_for_timeout(800)
                shot("view_info_bar_tooltip")
            r.step("View: a .bmp opens with the microscope info bar", view)

            def sets():
                page.goto(base + "/stitch")
                mark(page, "st_set_radio").wait_for(timeout=180_000)
                wait_idle(page, 2000)
                shot("stage_positions_no_axis_warning")
                mark(page, "st_no_stage").click()
                wait_idle(page, 2000)
                mark(page, "st_no_stage").hover()
                page.wait_for_timeout(800)
                shot("do_not_use_stage_positions_grid")
                r.no_hscroll("stitch")
            r.step("Stitch: 'Do not use stage positions'", sets)

            def run():
                mark(page, "st_run").click()
                mark(page, "st_summary_BSE").wait_for(timeout=900_000)
                wait_idle(page, 2500)
                page.mouse.move(10, 400)
                shot("result_zoomable_tile_borders")
                v = mark(page, "st_result_BSE")
                box = v.bounding_box()
                cx, cy = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.45
                mark(page, "st_seams_BSE").click()
                page.wait_for_timeout(1500)
                shot("result_borders_and_seams")
                page.mouse.move(cx, cy)
                for _ in range(6):
                    page.mouse.wheel(0, -300)
                    page.wait_for_timeout(500)
                page.wait_for_timeout(2500)
                shot("result_zoomed_in_seams")
                for _ in range(4):
                    page.mouse.wheel(0, -300)
                    page.wait_for_timeout(500)
                page.wait_for_timeout(2500)
                shot("result_zoomed_full_resolution")
                page.mouse.click(cx, cy)
                page.wait_for_timeout(1200)
                mark(page, "st_tile_panel").scroll_into_view_if_needed()
                page.wait_for_timeout(600)
                shot("result_click_selects_tile")
            r.step("Stitch: zoomable result", run)
            br.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
    errs = [x for x in r.logs if "pageerror" in x]
    print(f"\n{r.n} screenshots in {out}; {len(r.failed)} failed steps; {len(errs)} page errors")
    for f in r.failed:
        print("  FAILED", f)
    for x in r.logs:
        print("  ", x)
    return len(r.failed) + len(errs)


if __name__ == "__main__":
    sys.exit(main())
