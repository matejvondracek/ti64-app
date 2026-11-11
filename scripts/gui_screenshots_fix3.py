"""Screenshots of the screens changed by the third round of fixes after the phase 6.6 user test (top bar memory +
Feedback, Settings, EBSD load progress on the FULL JU02 map, KAM / GROD / GOS / GND in the map list, legends snapped
and per map kind, prior-beta grains on the prior beta tab, History right-click delete, Results right-click, grain-size
histogram range, export dialog with Pictures folder / earlier exports, image export wording). View / Stitch screens:
scripts/gui_screenshots_fix3_stitch.py.

Usage:  python scripts/gui_screenshots_fix3.py [--out projects/_screens_fix3/main] [--port 8097] [--headed]
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
from gui_screenshots import DATA, REPO, Run, choose, inp, mark, wait_idle  # noqa: E402


def wait_map(page, timeout_s: float = 600) -> None:
    mark(page, "map_viewer").wait_for(timeout=timeout_s * 1000)
    wait_idle(page, 1500)
    page.wait_for_timeout(1500)


def open_section(page, key: str) -> None:
    mark(page, f"sec_{key}").locator(".q-expansion-item__container > .q-item").first.click()
    page.wait_for_timeout(500)


def flow(r: Run, page, base: str, ang: Path, bmp: Path | None, work: Path) -> None:
    def home():
        page.goto(base + "/")
        wait_idle(page, 1200)
        r.shot("home_top_bar_memory_and_feedback")
        mark(page, "mem_use").hover()
        page.wait_for_timeout(900)
        r.shot("memory_tooltip")
    r.step("home", home)

    def project():
        r.queue([str(ang)] + ([str(bmp)] if bmp else []))
        mark(page, "home_new").click()
        page.wait_for_timeout(500)
        inp(page, "np_name").fill("fix3_demo")
        mark(page, "np_create").click()
        wait_idle(page, 1500)
        mark(page, "add_file").click()
        wait_idle(page, 1500)
    r.step("project with the full JU02 map", project)

    def load():
        page.goto(base + "/ebsd")
        mark(page, "load_stage").wait_for(timeout=60000)
        page.wait_for_timeout(1200)
        r.shot("ebsd_loading_progress_in_map_area")
        t0 = time.time()
        page.wait_for_function("() => { const e = document.querySelector('[data-testid=\"load_stages_done\"]');"
                               " return e && e.textContent.length > 0; }", timeout=120000)
        r.shot("ebsd_loading_progress_stages_done")
        wait_map(page)
        print(f"  first image after ~{time.time() - t0:.0f} s more")
        r.shot("ebsd_full_JU02_loaded")
    r.step("EBSD load with progress", load)

    def feedback():
        mark(page, "feedback_btn").click()
        mark(page, "feedback_dialog").wait_for()
        page.locator('[data-testid="fb_text"] textarea, textarea[data-testid="fb_text"]').first             .fill("The KAM map of JU02 looks noisy at grain boundaries.")
        mark(page, "fb_category").locator("button", has_text="Idea").click()
        mark(page, "fb_log").click()
        page.wait_for_timeout(600)
        r.shot("feedback_dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        mark(page, "settings_gear").click()
        mark(page, "settings_feedback").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("settings_feedback_section")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    r.step("Feedback + Settings", feedback)

    def maps_list():
        mark(page, "viewer_kind").click()
        page.wait_for_timeout(500)
        r.shot("map_list_kam_grod_gos_gnd_on_demand")
        page.keyboard.press("Escape")
        mark(page, "legend_btn_ipf_key").click()
        page.wait_for_timeout(3000)
        wait_idle(page, 1000)
        r.shot("ipf_map_ipf_key_and_scale_bar_snapped")
        mark(page, "legend_layout").click()
        page.wait_for_timeout(500)
        r.shot("legend_layout_menu")
        choose(page, "legend_anchor_scale_bar", "bottom right")
        page.wait_for_timeout(800)
        r.shot("scale_bar_snapped_bottom_right")
        page.keyboard.press("Escape")
        choose(page, "viewer_kind", "KAM")
        page.wait_for_timeout(1500)
        r.shot("kam_computed_on_first_use_progress")
        wait_map(page)
        r.shot("kam_map_colour_bar_no_ipf_key")
        open_section(page, "kam")
        r.shot("kam_section_parameters_only")
    r.step("maps list, legends, KAM on demand", maps_list)

    def grains_beta():
        open_section(page, "grains")
        mark(page, "run_grains").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("alpha_grains_stays_on_alpha_tab")
        open_section(page, "recon")
        mark(page, "run_recon").click()
        wait_idle(page, 3000)
        wait_map(page)
        r.shot("prior_beta_tab_after_reconstruction")
        open_section(page, "grains")
        r.shot("grains_section_on_prior_beta_tab")
        mark(page, "run_grains").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("prior_beta_grains_hagb_overlay_on_beta_tab")
        mark(page, "viewer_overlays").click()
        page.wait_for_timeout(500)
        r.shot("prior_beta_overlay_menu")
        page.keyboard.press("Escape")
    r.step("prior-beta grains (fix 10)", grains_beta)

    def history():
        mark(page, "mtab_alpha").click()
        wait_map(page)
        row = page.locator('[data-testid^="hist_"]').filter(has_text="Grains and boundaries").first
        row.click(button="right")
        page.wait_for_timeout(500)
        r.shot("history_right_click_menu")
        page.locator('[data-testid^="hist_delete_"]:visible').first.click()
        mark(page, "delete_one_dialog").wait_for()
        page.wait_for_timeout(400)
        r.shot("history_delete_with_dependents_dialog")
        mark(page, "delete_one_cancel").click()
    r.step("History right-click delete (fix 13)", history)

    def grainsize():
        open_section(page, "grainsize")
        mark(page, "run_gs").click()
        wait_idle(page, 3000)
        mark(page, "mtab_alpha").click()
        wait_map(page)
        if not mark(page, "grainsize_min_um").is_visible():
            open_section(page, "grainsize")
        inp(page, "grainsize_min_um").fill("0.5")
        inp(page, "grainsize_max_um").fill("10")
        inp(page, "grainsize_bin_um").fill("0.5")
        mark(page, "grainsize_min_um").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        r.shot("grain_size_histogram_min_max_bin")
        mark(page, "run_gs_fig").click()
        wait_idle(page, 2500)
        page.wait_for_timeout(1500)
        r.shot("histogram_with_range_on_results")
        page.locator('[data-testid="result_card"]').last.click(button="right")
        page.wait_for_timeout(500)
        r.shot("results_right_click_menu")
        page.locator('[data-testid="result_export"]:visible').first.click()
        mark(page, "result_export_dialog").wait_for()
        page.wait_for_timeout(400)
        r.shot("results_export_dialog")
        page.keyboard.press("Escape")
    r.step("grain size + results menu (fix 14, 15)", grainsize)

    def export():
        mark(page, "mtab_alpha").click()
        wait_map(page)
        mark(page, "export_btn").click()
        mark(page, "export_dialog").wait_for()
        page.wait_for_timeout(500)
        r.shot("export_dialog_pictures_folder_view_defaults")
        target = work / "exports" / "JU02_ipf.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        inp(page, "exp_dest").fill(str(target))
        mark(page, "run_export").click()
        page.locator(".q-notification", has_text="Exported").first.wait_for(timeout=300000)
        page.wait_for_timeout(800)
        r.shot("exported_notification")
        mark(page, "export_btn").click()
        mark(page, "export_dialog").wait_for()
        mark(page, "exp_earlier").click()
        page.wait_for_timeout(600)
        r.shot("export_dialog_earlier_exports_export_again")
        page.keyboard.press("Escape")
        open_section(page, "cleanup")
        page.wait_for_timeout(300)
        r.shot("history_without_export_steps")
    r.step("export log (fix 3, 4, 5)", export)

    if bmp:
        def image_export():
            page.goto(base + "/view")
            mark(page, "view_viewer").wait_for(timeout=60000)
            wait_idle(page, 1500)
            mark(page, "view_export").click()
            mark(page, "export_dialog").wait_for()
            mark(page, "exp_strip").hover()
            page.wait_for_timeout(900)
            r.shot("image_export_microscope_info_bar_wording")
            page.keyboard.press("Escape")
        r.step("image export wording", image_export)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_fix3" / "main"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix3"))
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    for d in (out, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    ang = sorted(DATA.glob("*JU02*.ang"))
    if not ang:
        print("test data missing: data/test/*JU02*.ang")
        return 1
    bmp = next(iter(sorted((DATA / "stitch").glob("*.bmp"))), None) if (DATA / "stitch").is_dir() else None
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
            flow(r, page, base, ang[0], bmp, work)
            r.no_hscroll("ebsd")
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
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
