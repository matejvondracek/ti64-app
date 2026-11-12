"""Screenshots of the screens changed by the second round of fixes after the phase 6.6 user test (EBSD tab, History,
legends, grain size per map tab, replay progress, delete steps, View .up2 overview, Stitch). Playwright, browser mode,
Windows dialogs mocked; output projects/_gui_screenshots_fix2/ with index.html.

Usage:  python scripts/gui_screenshots_fix2.py [--out projects/_gui_screenshots_fix2] [--port 8096] [--headed]
        [--only ebsd,view,stitch]
"""
from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_screenshots import DATA, REPO, Run, choose, inp, mark, wait_idle  # noqa: E402


def wait_loaded(page, timeout_s: float = 300) -> None:
    mark(page, "map_viewer").wait_for(timeout=timeout_s * 1000)
    wait_idle(page, 1500)


def notify_text(page) -> str:
    return " | ".join(page.locator(".q-notification").all_inner_texts())


def ebsd_flow(r: Run, page, base: str, crop: Path) -> None:
    def start():
        r.queue([str(crop)])
        page.goto(base + "/")
        mark(page, "home_new").click()
        page.wait_for_timeout(500)
        inp(page, "np_name").fill("fix2_demo")
        mark(page, "np_create").click()
        wait_idle(page, 1500)
        mark(page, "add_file").click()
        wait_idle(page, 1500)
        row = page.locator('[data-testid^="file_"]').first
        row.hover()
        page.wait_for_timeout(400)
        r.shot("files_linked_nothing_entered")
    r.step("project with a linked .ang, nothing entered", start)

    def autoload():
        page.goto(base + "/ebsd")
        mark(page, "viewer_loading").wait_for(timeout=30000)
        r.shot("ebsd_opens_and_loads_by_itself")
        wait_loaded(page)
        r.shot("ebsd_loaded_without_sample_or_build_direction")
        mark(page, "viewer_kind").click()
        page.wait_for_timeout(400)
        r.shot("map_list_only_what_exists_no_fit_prias")
        page.keyboard.press("Escape")
        mark(page, "viewer_overlays").hover()
        page.wait_for_timeout(600)
        r.shot("overlays_disabled_before_grains")
    r.step("EBSD tab: auto-load", autoload)

    def options():
        mark(page, "sec_cleanup").click() if not mark(page, "cleanup_hq_alpha").is_visible() else None
        page.wait_for_timeout(300)
        r.shot("cleanup_options_off_parameters_hidden")
        mark(page, "cleanup_hq_filter").click()
        mark(page, "cleanup_spike_removal").click()
        page.wait_for_timeout(300)
        r.shot("cleanup_options_on_parameters_shown")
        mark(page, "cleanup_hq_filter").click()
        mark(page, "cleanup_spike_removal").click()
        mark(page, "sec_kam").click()
        page.wait_for_timeout(400)
        r.shot("kam_section_kam_parameters")
        choose(page, "kam_kind", "GND")
        r.shot("kam_section_gnd_parameters")
        choose(page, "kam_kind", "KAM")
    r.step("edit menu: options hide their parameters", options)

    def bd_needed():
        mark(page, "sec_ipf").click()
        page.wait_for_timeout(400)
        choose(page, "ipf_dir_alpha", "build")
        mark(page, "viewer_apply").click()
        page.locator(".q-notification", has_text="build direction").first.wait_for(timeout=60000)
        page.wait_for_timeout(500)
        r.shot("build_direction_asked_with_link")
        page.locator(".q-notification button", has_text="Open map sheet").first.click()
        mark(page, "ms_bd_angle").wait_for(timeout=30000)
        wait_idle(page, 800)
        r.shot("link_opens_the_map_sheet")
        page.goto(base + "/ebsd")
        wait_loaded(page)
        mark(page, "sec_ipf").click()
        page.wait_for_timeout(300)
        choose(page, "ipf_dir_alpha", "Z")
    r.step("build direction only when a step needs it", bd_needed)

    def grains_zoom():
        page.goto(base + "/ebsd")                    # fresh view (fit) after the Files tab
        wait_loaded(page)
        mark(page, "sec_grains").click()
        page.wait_for_timeout(300)
        mark(page, "run_grains").click()
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        r.shot("grains_hagb_overlay")
        mark(page, "viewer_overlays").click()
        page.wait_for_timeout(500)
        r.shot("alpha_overlay_menu_no_beta_no_crack_yet")
        page.keyboard.press("Escape")
        v = mark(page, "map_viewer")
        box = v.bounding_box()
        for _ in range(3):
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(700)
        wait_idle(page, 1500)
        page.wait_for_timeout(1200)
        r.shot("boundaries_zoomed_in_same_line_width")
    r.step("boundary lines keep their screen width", grains_zoom)

    def legends():
        for k in ("colorbar", "ipf_key"):
            mark(page, f"legend_btn_{k}").click()
            page.wait_for_timeout(800)
        wait_idle(page, 1000)
        r.shot("legends_on_map_overview")
        v = mark(page, "map_viewer")
        box = v.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.75)
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(900)
        wait_idle(page, 1200)
        r.shot("legends_zoomed_in_grow_with_map")
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 150, box["y"] + box["height"] / 2 + 60, steps=8)
        page.mouse.up()
        page.wait_for_timeout(800)
        r.shot("legends_panned_move_with_map")
    r.step("legends anchored to the map", legends)

    def recon():
        mark(page, "sec_recon").click()
        page.wait_for_timeout(400)
        mark(page, "run_recon").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("beta_tab_prior_beta_boundaries")
        mark(page, "viewer_overlays").click()
        page.wait_for_timeout(500)
        r.shot("beta_overlay_menu_parent_grain_boundaries_only")
        page.keyboard.press("Escape")
    r.step("prior-beta tab shows prior-beta boundaries", recon)

    def grainsize_beta():
        mark(page, "sec_grainsize").click()
        page.wait_for_timeout(500)
        r.shot("grain_size_section_on_prior_beta_tab")
        mark(page, "run_gs").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("prior_beta_grain_size_result_needs_bd")
        mark(page, "mtab_beta").click()
        wait_idle(page, 1500)
        mark(page, "sec_grainsize").scroll_into_view_if_needed()
        inp(page, "grainsize_bin_um").fill("10")
        inp(page, "grainsize_bin_um").press("Enter")
        mark(page, "run_gs_fig").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("prior_beta_ecd_histogram_bin_10um")
    r.step("grain size on the prior-beta tab", grainsize_beta)

    def grainsize_alpha():
        mark(page, "mtab_alpha").click()
        wait_idle(page, 1500)
        mark(page, "sec_grainsize").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        r.shot("grain_size_section_on_alpha_tab")
        mark(page, "run_gs").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("alpha_grain_size_result")
        mark(page, "mtab_alpha").click()
        wait_idle(page, 1500)
        mark(page, "sec_grainsize").scroll_into_view_if_needed()
        inp(page, "grainsize_bin_um").fill("0.5")
        inp(page, "grainsize_bin_um").press("Enter")
        mark(page, "run_gs_fig").click()
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("alpha_ecd_histogram_bin_0_5um")
    r.step("grain size on the alpha tab", grainsize_alpha)

    def history_delete():
        # the input state set on the Files tab (raw) makes every step of the map stale; the EBSD tab loads it again
        page.goto(base + "/files?sel=JU02_C_Area3")
        mark(page, "ms_input_state").wait_for(timeout=30000)
        mark(page, "ms_input_state").locator("button", has_text="raw").first.click()
        wait_idle(page, 1500)
        page.goto(base + "/ebsd")
        wait_loaded(page)
        r.shot("history_delete_buttons_with_stale")
        b = mark(page, "delete_stale_steps")
        if b.count():
            b.click()
            page.wait_for_timeout(600)
            r.shot("delete_stale_dialog")
            mark(page, "delete_cancel").click()
            page.wait_for_timeout(300)
        mark(page, "delete_all_steps").click()
        page.wait_for_timeout(600)
        r.shot("delete_all_dialog")
        mark(page, "delete_whole").click()
        page.wait_for_timeout(400)
        r.shot("delete_all_dialog_whole_project")
        mark(page, "delete_cancel").click()
    r.step("History: delete all / stale steps", history_delete)

    def replay():
        page.locator(".q-expansion-item", has_text="Replay and recipe files").first.click()
        page.wait_for_timeout(400)
        page.locator(".q-checkbox", has_text="include stale steps").first.click()   # long enough to see the dialog
        page.wait_for_timeout(300)
        mark(page, "replay").click()
        mark(page, "replay_dialog").wait_for(timeout=30000)
        page.wait_for_timeout(4000)
        r.shot("replay_progress_step_bar_elapsed_cancel")
        mark(page, "replay_result").wait_for(timeout=900_000)
        wait_idle(page, 800)
        mark(page, "replay_result").scroll_into_view_if_needed()
        r.shot("replay_result_with_time")
    r.step("replay with progress", replay)


def stitch_flow(r: Run, page, base: str, folder: Path) -> None:
    def scan():
        page.goto(base + "/stitch")
        mark(page, "stitch_left").wait_for()
        r.queue(str(folder))
        choose(page, "st_source", "A folder")
        page.wait_for_timeout(500)
        inp(page, "st_folder").fill(str(folder))
        mark(page, "st_scan").click()
        mark(page, "st_set_radio").wait_for(timeout=180_000)
        wait_idle(page, 2000)
        r.shot("stitch_JU04_unknown_axis_and_repeats_one_stitch_button")
        mark(page, "st_stage_check").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        r.shot("stitch_stage_check_box")
        mark(page, "st_tab_arrange").click()
        page.wait_for_timeout(2500)
        r.shot("stitch_arrange_same_positions_spread")
        r.no_hscroll("stitch")
    r.step("Stitch: JU04 crack path, unknown stage axis", scan)

    def run():
        mark(page, "st_run").click()
        mark(page, "st_summary_BSE").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.mouse.move(700, 700)
        page.wait_for_timeout(1500)
        r.shot("stitch_JU04_result")
        n = mark(page, "st_notes_BSE")
        if n.count():
            n.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            r.shot("stitch_JU04_result_notes")
    r.step("Stitch: one button", run)

    def export():
        mark(page, "st_export").click()
        mark(page, "export_dialog").wait_for()
        page.wait_for_timeout(800)
        r.shot("mosaic_export_dialog")
        mark(page, "exp_mode").click()
        page.wait_for_timeout(500)
        r.shot("mosaic_export_fractions")
        page.locator(".q-menu .q-item", has_text="1/4").first.click()
        page.wait_for_timeout(500)
        mark(page, "exp_fmt").click()
        page.wait_for_timeout(500)
        r.shot("mosaic_export_formats_bigtiff")
        fmt = page.locator(".q-menu .q-item", has_text="PNG")
        if fmt.count():
            fmt.first.click()
        else:
            mark(page, "exp_fmt").locator("button", has_text="PNG").first.click()
        page.wait_for_timeout(400)
        st = mark(page, "exp_strip")
        if st.count():
            st.click()
            page.wait_for_timeout(500)
        r.shot("mosaic_export_strip_one_scale_bar_note")
        mark(page, "exp_cancel").click()
    r.step("Stitch: export dialog", export)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--out", default=str(REPO / "projects" / "_gui_screenshots_fix2"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix2"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--only", default="ebsd,stitch")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    for d in (out, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    crop = sorted((DATA / "crops").glob("*JU02*_crop1000.ang"))
    if not crop:
        print("test data missing: data/test/crops/*JU02*_crop1000.ang")
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
            if "ebsd" in a.only:
                ebsd_flow(r, page, base, crop[0])
                r.no_hscroll("ebsd")
            ju04 = REPO / "data" / "JU04" / "stich along crack path"
            if "stitch" in a.only and ju04.is_dir():
                stitch_flow(r, page, base, ju04)
            br.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
    errs = [x for x in r.logs if "pageerror" in x]
    up2 = REPO / "projects" / "_up2_gui_shots"                 # View tab .up2 screens (scratch shot_up2.py run)
    for k, f in enumerate(sorted(up2.glob("*.png")) if up2.is_dir() else []):
        shutil.copy2(f, out / f"90_{k:02d}_view_up2_{f.stem}.png")
    shots = sorted(out.glob("*.png"))
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>fix 2 screenshots</title><body style='font:14px sans-serif'>"
        + "".join(f"<h3>{html.escape(f.stem)}</h3><img src='{f.name}' style='max-width:100%;border:1px solid #ccc'>"
                  for f in shots), encoding="utf-8")
    print(f"\n{r.n} screenshots in {out}; {len(r.failed)} failed steps; {len(errs)} page errors")
    for f in r.failed:
        print("  FAILED", f)
    for x in r.logs:
        print("  ", x)
    return len(r.failed) + len(errs)


if __name__ == "__main__":
    sys.exit(main())
