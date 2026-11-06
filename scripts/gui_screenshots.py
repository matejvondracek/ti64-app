"""Click through the phase 6.6 GUI (with the fixes after the user test) with Playwright (browser mode, Windows dialogs mocked) and save a screenshot of
every screen: home -> new project -> add files -> samples (auto-assign, sheets) -> EBSD (load, cleanup Apply, hold
Compare to original, sections, export dialog) -> Settings -> Stitch (layout, stitch, export) -> Crack; then the main
screens again at 1920x1080. Checks that no page scrolls horizontally at 1366x768 and that there are no page errors.

Usage:  python scripts/gui_screenshots.py [--out projects/_gui_screenshots] [--work projects/_gui_demo]
                                          [--port 8095] [--headed] [--no-stitch]
Needs the test data (data/test/crops/*JU02*_crop1000.ang, data/test/stitch/) and Playwright with Chromium
(`python -m playwright install chromium`). Starts the GUI itself (`python -m ti64app.gui --browser`) and stops it.
Every step that fails is reported and the run continues; the exit code is the number of failed steps.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

print = functools.partial(print, flush=True)  # noqa: A001  (progress lines appear while it runs)
REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "test"


class Run:
    def __init__(self, page, out: Path, mock: Path) -> None:
        self.page, self.out, self.mock, self.n, self.failed = page, out, mock, 0, []
        self.logs: list[str] = []
        page.on("console", lambda m: m.type == "error" and self.logs.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: self.logs.append(f"pageerror: {e}"))

    def shot(self, name: str, full: bool = False) -> None:
        self.n += 1
        f = self.out / f"{self.n:02d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.png"
        self.page.screenshot(path=str(f), full_page=full)
        print(f"  saved {f.name}")

    def step(self, name: str, fn) -> None:
        print(f"- {name}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.failed.append(f"{name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                self.shot(f"FAILED_{name}")
            except Exception:  # noqa: BLE001
                pass

    def queue(self, *answers) -> None:
        """Answers of the next Windows dialogs (TI64_MOCK_DIALOGS)."""
        d = json.loads(self.mock.read_text(encoding="utf-8")) if self.mock.is_file() else {}
        d["queue"] = list(answers)
        self.mock.write_text(json.dumps(d, indent=1), encoding="utf-8")

    def no_hscroll(self, where: str) -> None:
        sw, cw = self.page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        if sw > cw + 1:
            self.failed.append(f"horizontal scrolling on {where}: {sw} > {cw} px")
            print(f"  ! horizontal scroll {sw} > {cw}")


def mark(page, name: str):
    return page.locator(f'[data-testid="{name}"]').first


def inp(page, name: str):
    """The native <input> of a NiceGUI input / number (Quasar puts data-testid on the input itself)."""
    return page.locator(f'input[data-testid="{name}"], [data-testid="{name}"] input').first


def wait_idle(page, ms: int = 500, timeout_s: float = 600) -> None:
    page.wait_for_timeout(ms)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        busy = page.locator('[data-testid="apply_progress"]:visible').count() + \
            page.locator(".q-notification .q-spinner").count()
        if busy == 0:
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(300)


def choose(page, testid: str, option: str) -> None:
    """Pick an option of a NiceGUI (Quasar) select."""
    mark(page, testid).click()
    page.locator(".q-menu .q-item", has_text=option).first.click()
    page.wait_for_timeout(300)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--out", default=str(REPO / "projects" / "_gui_screenshots"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-stitch", action="store_true", help="skip stitching (faster)")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    for d in (out, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    crop = sorted((DATA / "crops").glob("*JU02*_crop1000.ang"))
    stitch = sorted((DATA / "stitch").glob("*.bmp")) + sorted((DATA / "stitch").glob("*.txt"))
    if not crop:
        print("test data missing: data/test/crops/*JU02*_crop1000.ang (python scripts/make_crops.py)")
        return 1
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
            ju04 = [f for f in sorted((REPO / "data" / "JU04").glob("*BED-C*")) if f.suffix.lower() in (".bmp", ".txt")]
            up2 = next(iter(sorted(DATA.glob("*.up2"))), None)
            flow(r, page, base, work, crop[0], stitch, a.no_stitch, ju04, up2)
            # same screens at 1920 x 1080
            page.set_viewport_size({"width": 1920, "height": 1080})
            for path, name in (("/files", "files"), ("/view", "view"), ("/ebsd", "ebsd"), ("/stitch", "stitch")):
                r.step(f"1920 {name}", lambda path=path, name=name: (
                    page.goto(base + path), wait_idle(page, 1500), r.shot(f"1920x1080_{name}"),
                    r.no_hscroll(f"{name} 1920")))
            page.goto(base + "/")
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


def flow(r: Run, page, base: str, work: Path, crop: Path, stitch: list[Path], no_stitch: bool,
         ju04: list[Path], up2: Path | None) -> None:
    def home():
        page.goto(base + "/")
        mark(page, "home_title").wait_for()
        page.wait_for_timeout(500)
        r.shot("home")
        r.no_hscroll("home")
    r.step("home", home)

    def new_project():
        r.queue(str(work / "projects"))
        mark(page, "home_new").click()
        inp(page, "np_name").fill("Ti64_demo")
        mark(page, "np_browse").click()
        page.wait_for_timeout(500)
        mark(page, "np_create").click()
        mark(page, "tab_files").wait_for()
        page.wait_for_timeout(600)
        r.shot("files_empty_tabs_with_View")
    r.step("new project", new_project)

    def add_files():
        # one copy of a SEM image that is then moved away: "Find missing…"
        lost = work / "lost" / stitch[0].name
        lost.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stitch[0], lost)
        files = [str(crop)] + [str(f) for f in stitch] + [str(f) for f in ju04] + ([str(up2)] if up2 else []) \
            + [str(lost)]
        r.queue(files)
        mark(page, "add_file").click()
        page.wait_for_selector("text=file(s) linked")
        mark(page, "auto_assign").click()
        page.wait_for_selector("text=Auto-assign files to samples")
        mark(page, "aa_confirm").click()
        page.wait_for_selector('[data-testid="sample_JU05"]')
        page.wait_for_timeout(600)
        moved = work / "archive" / "2026" / lost.name
        moved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(lost), moved)
        page.reload()
        mark(page, "relocate_all").wait_for()
        page.wait_for_timeout(600)
        r.shot("files_missing_file")
        r.no_hscroll("files")
        r.queue(str(work / "archive"))
        page.locator('[data-testid^="relocate_"]').nth(1).click()
        mark(page, "relocate_dialog").wait_for()
        page.wait_for_timeout(500)
        r.shot("find_missing_dialog")
        mark(page, "reloc_go").click()
        page.wait_for_selector("text=file(s) relinked")
        page.wait_for_timeout(800)
        if page.locator('[data-testid="relocate_all"]:visible').count():
            raise AssertionError("missing file still shown after relinking (refresh bug)")
        r.shot("find_missing_relinked_immediately")
    r.step("add files, find missing", add_files)

    def files_tree():
        # right-click "New sample for this file"
        row = page.locator('[data-testid^="file_9772_MVH1_JU05_BED"]').first
        row.click(button="right")
        page.wait_for_timeout(400)
        r.shot("context_menu")
        page.locator(".q-menu .q-item", has_text="New sample for this file").first.click()
        mark(page, "new_sample_dialog").wait_for()
        inp(page, "ns_id").fill("JU05b")
        page.wait_for_timeout(300)
        r.shot("new_sample_for_file_dialog")
        mark(page, "ns_create").click()
        page.wait_for_selector('[data-testid="sample_JU05b"]')
        page.wait_for_timeout(600)
        r.shot("new_sample_for_file_done")
        page.locator('[data-testid^="file_9772_MVH1_JU05_BED"]').first.click(button="right")
        page.wait_for_timeout(300)
        page.locator(".q-menu .q-item", has_text="Move to sample JU05").first.click()
        page.wait_for_timeout(600)
        mark(page, "name_sample_JU04").click()                 # fold JU04
        page.wait_for_timeout(600)
        r.shot("sample_JU04_folded")
        mark(page, "name_sample_JU04").click()
        page.wait_for_timeout(1000)
        # map sheet of the EBSD crop
        page.locator('[data-testid^="file_JU02"]').first.click()
        page.locator('[data-testid="sheet_map"]', has_text="JU02").wait_for()
        mark(page, "ms_input_state").locator("button", has_text="OIM-cleaned").click()
        inp(page, "ms_bd_angle").fill("270")
        inp(page, "ms_bd_angle").press("Enter")
        page.wait_for_timeout(600)
    r.step("files tree", files_tree)

    def view_tab():
        page.locator('[data-testid^="file_9771_MVH1_JU05_BED"]').first.dblclick()
        mark(page, "view_viewer").wait_for(timeout=60_000)
        page.wait_for_timeout(2500)
        r.shot("view_image")
        r.no_hscroll("view")
        lg = mark(page, "legend_scale_bar")
        box = lg.bounding_box()
        if box:
            page.mouse.move(box["x"] + 10, box["y"] + 6)
            page.mouse.down()
            page.mouse.move(box["x"] + 420, box["y"] - 380, steps=8)
            page.mouse.up()
            page.wait_for_timeout(600)
        mark(page, "view_viewer").hover()
        for _ in range(3):
            page.mouse.wheel(0, -240)
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
        r.shot("view_image_zoomed_scale_bar_moved")
        if up2:
            choose(page, "view_source", Path(up2).name)
            mark(page, "up2_viewer").wait_for(timeout=60_000)
            page.wait_for_timeout(1200)
            r.shot("view_up2_pattern")
            mark(page, "up2_right").click()
            page.wait_for_timeout(800)
            r.shot("view_up2_next_pattern")
    r.step("View tab", view_tab)

    def ebsd_load():
        mark(page, "tab_ebsd").click()
        mark(page, "edit_menu").wait_for()
        choose(page, "ebsd_file", "JU02")
        page.wait_for_timeout(800)
        mark(page, "run_load").click()
        wait_idle(page, 1500)
        mark(page, "map_viewer").wait_for(timeout=300_000)
        page.wait_for_timeout(2500)
        r.shot("ebsd_loaded_history_right")
        r.no_hscroll("ebsd")
        inp(page, "cleanup_noise_max_void_px").fill("6")
        inp(page, "cleanup_noise_max_void_px").blur()
        mark(page, "run_cleanup").click()
        wait_idle(page, 1000)
        page.wait_for_timeout(2000)
    r.step("EBSD load + cleanup", ebsd_load)

    def grains_overlays():
        mark(page, "sec_grains").locator(".q-item").first.click()
        page.wait_for_timeout(300)
        mark(page, "run_grains").click()
        wait_idle(page, 1000)
        page.wait_for_timeout(2000)
        mark(page, "viewer_overlays").click()
        page.wait_for_timeout(600)
        r.shot("overlay_menu_columns")
        mark(page, "ov_interface").click()
        page.wait_for_timeout(300)
        w = inp(page, "ov_width_hagb")
        w.fill("1.5")
        w.press("Enter")
        c = inp(page, "ov_color_hagb")
        c.fill("#1d4ed8")
        c.press("Enter")
        page.wait_for_timeout(1500)
        r.shot("overlay_menu_styles")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        r.shot("map_with_styled_boundaries")
    r.step("grains + overlay menu", grains_overlays)

    def legends_hex():
        mark(page, "legend_btn_colorbar").click()
        page.wait_for_timeout(600)
        mark(page, "legend_btn_ipf_key").click()
        page.wait_for_selector('[data-testid="legend_ipf_key"] img', timeout=120_000)
        page.wait_for_timeout(1500)
        lg = mark(page, "legend_ipf_key")
        box = lg.bounding_box()
        if box:
            page.mouse.move(box["x"] + 20, box["y"] + 20)
            page.mouse.down()
            page.mouse.move(box["x"] - 300, box["y"] + 40, steps=8)
            page.mouse.up()
        page.wait_for_timeout(800)
        r.shot("legends_on_map")
        mark(page, "map_viewer").hover()
        for _ in range(5):
            page.mouse.wheel(0, -240)
            page.wait_for_timeout(600)
        page.wait_for_timeout(2500)
        mark(page, "viewer_zoominfo").hover()
        page.wait_for_timeout(1200)
        r.shot("viewer_hexagons_zoom_tooltip")
        mode = mark(page, "viewer_zoominfo").get_attribute("data-mode")
        if mode != "hexagons":
            raise AssertionError(f"zoomed in, the viewer should draw hexagons, not {mode}")
        for _ in range(4):
            page.mouse.wheel(0, 240)
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
        r.shot("viewer_brick_zoomed_out")
    r.step("legends + hexagons", legends_hex)

    def export():
        target = work / "exports" / "JU02_ipf_export.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        r.queue(str(target))
        mark(page, "export_btn").click()
        mark(page, "export_dialog").wait_for()
        mark(page, "exp_browse").click()
        page.wait_for_timeout(500)
        r.shot("export_dialog_native")
        choose(page, "exp_mode", "size in pixels")
        inp(page, "exp_px_h").fill("1500")
        inp(page, "exp_px_h").blur()
        page.wait_for_timeout(600)
        r.shot("export_dialog_px_height_typed")
        choose(page, "exp_mode", "8×")
        page.wait_for_timeout(500)
        mark(page, "exp_hex_info").hover()
        page.wait_for_timeout(900)
        r.shot("export_dialog_8x_hex_tooltip")
        choose(page, "exp_mode", "4×")
        mark(page, "run_export").click()
        page.wait_for_selector("text=Exported", timeout=300_000)
        r.shot("export_done")
        if not target.is_file():
            raise AssertionError(f"export not written: {target}")
        shutil.copy2(target, r.out / "export_result_JU02_4x_legends.png")
    r.step("export dialog", export)

    def history_reorder():
        page.wait_for_timeout(9000)                     # let the export notifications fade
        hist = mark(page, "recipe_table")
        rows = hist.locator('[data-testid^="hist_"]')
        n = rows.count()
        if n < 3:
            raise AssertionError(f"history shows {n} steps")
        r.shot("history_panel")
        # invalid: Cleanup before Load map
        rows.nth(1).drag_to(rows.nth(0))
        page.wait_for_selector("text=This order is not possible", timeout=10_000)
        page.wait_for_timeout(400)
        r.shot("history_reorder_refused")
        page.wait_for_timeout(2000)
        # valid: grains on the loaded map before the cleanup -> rerun
        rows = mark(page, "recipe_table").locator('[data-testid^="hist_"]')
        rows.nth(2).drag_to(rows.nth(1))
        mark(page, "reorder_dialog").wait_for()
        page.wait_for_timeout(400)
        r.shot("history_reorder_confirm")
        mark(page, "reorder_ok").click()
        page.wait_for_selector("text=New order applied", timeout=300_000)
        page.wait_for_timeout(2500)
        r.shot("history_reordered_stale_kept")
        mark(page, "undo").click()
        page.wait_for_timeout(4000)
        mark(page, "map_viewer").wait_for(timeout=120_000)
        page.wait_for_timeout(1500)
        r.shot("history_reorder_undone")
    r.step("history reorder", history_reorder)

    def recon():
        mark(page, "sec_recon").locator(".q-item").first.click()
        page.wait_for_timeout(400)
        mark(page, "run_recon").click()
        page.wait_for_selector("text=Prior-β map shown", timeout=900_000)
        page.wait_for_timeout(3000)
        r.shot("recon_beta_tab_no_boundaries")
        if mark(page, "mtab_beta").get_attribute("aria-selected") != "true":
            raise AssertionError("after the reconstruction the prior-beta tab should be focused")
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(2500)
        r.shot("alpha_tab_own_overlays_history")
        mark(page, "mtab_beta").click()
        page.wait_for_timeout(2500)
        mark(page, "rtab_results").click()
        page.wait_for_timeout(800)
        r.shot("results_recon_summary")
        mark(page, "mtab_beta").click()
        page.wait_for_timeout(1500)
    r.step("prior-beta map tab", recon)

    def settings():
        mark(page, "settings_gear").click()
        mark(page, "settings_dialog").wait_for()
        page.wait_for_timeout(600)
        r.shot("settings")
        page.keyboard.press("Escape")
    r.step("settings", settings)

    def stitch_tab():
        mark(page, "tab_stitch").click()
        mark(page, "stitch_left").wait_for()
        choose(page, "st_source", "All images of sample JU04")
        page.wait_for_timeout(2500)
        mark(page, "st_set_radio").wait_for(timeout=120_000)
        page.wait_for_timeout(1500)
        r.shot("stitch_sets_JU04_magnification_series")
        r.no_hscroll("stitch")
        mark(page, "st_strip").click()
        page.wait_for_timeout(400)
        page.locator(".q-menu .q-item", has_text="keep the original").first.hover()
        page.wait_for_timeout(1000)
        r.shot("stitch_info_strip_keep_greyed_tooltip")
        page.keyboard.press("Escape")
        choose(page, "st_source", "All images of sample JU05")
        page.wait_for_timeout(3000)
        mark(page, "st_arrange").wait_for(timeout=120_000)
        page.wait_for_timeout(1500)
        r.shot("stitch_arrange_JU05")
        # drag one tile roughly, lock another, box-select
        tiles = page.locator('.ti64-arrange [data-key]')
        t = tiles.nth(3)
        b = t.bounding_box()
        if b:
            page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            page.mouse.down()
            page.mouse.move(b["x"] + b["width"] / 2 + 4, b["y"] + b["height"] / 2 + 2, steps=4)
            page.mouse.up()
        page.wait_for_timeout(500)
        tiles.nth(0).click()
        mark(page, "st_lock").click()
        page.wait_for_timeout(300)
        mark(page, "st_all").click()
        page.wait_for_timeout(800)
        r.shot("stitch_arrange_moved_locked")
        if no_stitch:
            return
        mark(page, "st_refine").click()
        mark(page, "st_summary_BSE").wait_for(timeout=900_000)
        page.mouse.move(700, 700)
        page.wait_for_timeout(1500)
        r.shot("stitch_result_refined")
        mark(page, "st_pairs_BSE").locator("tbody tr").first.click()
        mark(page, "st_overlap_img").wait_for(timeout=120_000)
        page.wait_for_timeout(1200)
        r.shot("stitch_overlap_difference")
        target = work / "exports" / "JU05_mosaic.png"
        r.queue(str(target))
        mark(page, "st_export").click()
        mark(page, "export_dialog").wait_for()
        mark(page, "exp_browse").click()
        page.wait_for_timeout(500)
        r.shot("stitch_export_dialog")
        mark(page, "run_export").click()
        page.wait_for_selector("text=Exported", timeout=300_000)
        if not target.is_file():
            raise AssertionError(f"mosaic export not written: {target}")
        mark(page, "st_tab_result").click()
        page.wait_for_timeout(800)
        mark(page, "st_view_BSE").click()
        mark(page, "view_viewer").wait_for(timeout=120_000)
        page.wait_for_timeout(2500)
        r.shot("view_mosaic")
    r.step("stitch", stitch_tab)

    def crack():
        mark(page, "tab_crack").click()
        mark(page, "crack_placeholder").wait_for()
        r.shot("crack_placeholder")
    r.step("crack", crack)


if __name__ == "__main__":
    sys.exit(main())
