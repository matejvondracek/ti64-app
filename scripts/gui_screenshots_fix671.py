"""Screenshots of phase 6.7 fix 1 on the real maps: JU04_area1 (raw APEX, alpha + beta, 247 k points, no BD / position
entered) and JU04_area3 (raw APEX, alpha only, 2.6 M points, map axes 'approximate' L / S / T with BD = S).

Files tab: map axes exact (angle -> only the angle field; vector -> x y z) / approximate (labels + BD axis).
EBSD tab: 'Map' tab + axis labels in the map info, cleanup (dilation single iteration on; Grain CI standardization),
texture without BD / position (map axes by default, unavailable directions greyed with a tooltip), grain-size figure
as view (no History step) with and without the log-normal fit, deleting a figure from Results, ROI hover texts,
prior-beta reconstruction of the alpha-only map, retained beta 'not in file', texture / elastic named L / S / T,
Settings 'Parent beta phase'.

Usage:  python scripts/gui_screenshots_fix671.py [--out projects/_screens_fix671] [--port 8107] [--headed]
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
from gui_screenshots import REPO, Run, choose, inp, mark, wait_idle  # noqa: E402

JU04 = REPO / "data" / "JU04" / "EBSD"


def prepare(work: Path) -> str:
    env_backup = dict(os.environ)
    os.environ["TI64_PROJECTS_DIR"] = str(work / "projects")
    os.environ["TI64_PREFS"] = str(work / "prefs" / "preferences.json")
    sys.path.insert(0, str(REPO / "python"))
    from ti64app import service
    from ti64app.project import Project
    from ti64app.sample import MapAxes, SampleInfo

    p = Project.create("fix671_demo", base=work / "projects")
    t = time.time()
    p.save_sample_info(SampleInfo(id="JU04"))
    for a in ("area1", "area3"):
        service.run(p, "sample.set", {"map_id": f"JU04_{a}", "specimen_id": "JU04", "map_file": str(JU04 / f"JU04_{a}.ang"),
                                      "input_state": "raw"})
        p.update_file(f"JU04_{a}", input_state="raw", indexing="hough", indexing_asked=True)
    p.update_file("JU04_area3", axes=MapAxes(mode="approximate", x_label="L", y_label="S", z_label="T",
                                             build_axis="y"))
    service.run(p, "ebsd.load", {"map_id": "JU04_area1"})
    service.run(p, "ebsd.clean", {"map_id": "JU04_area1", "operation": "ci_threshold", "params": {}})
    service.run(p, "ebsd.clean", {"map_id": "JU04_area1", "operation": "grain_dilation", "params": {}})
    service.run(p, "grains.calc", {"map_id": "JU04_area1"})
    service.run(p, "grainsize.run", {"map_id": "JU04_area1", "target": "alpha"})
    service.run(p, "ebsd.load", {"map_id": "JU04_area3"})
    service.add_default_cleanup(p, "JU04_area3")
    service.run(p, "grains.calc", {"map_id": "JU04_area3"})
    r = service.run(p, "recon.run", {"map_id": "JU04_area3"})            # bug 1: failed before (no beta in the file)
    service.run(p, "grains.calc", {"map_id": "JU04_area3", "source": r.id})
    print(f"  prepared in {time.time() - t:.0f} s")
    os.environ.clear()
    os.environ.update(env_backup)
    return p.root.name


def img_loaded(page) -> None:
    """Wait until the newest result image has loaded (screenshots must not show an empty card)."""
    page.wait_for_function("() => { const i = [...document.querySelectorAll('[data-testid=result_card] img')];"
                           " return i.length && i[i.length - 1].complete && i[i.length - 1].naturalWidth > 0; }",
                           timeout=120_000)
    page.wait_for_timeout(800)


def flow(r: Run, page, base: str, pname: str, only: set[str] | None = None) -> None:
    step_ = r.step
    r.step = lambda name, fn: step_(name, fn) if (not only or name == "open project"
                                                    or any(o.lower() in name.lower() for o in only)) else None
    def files_sel(name: str):
        page.goto(base + "/files")
        wait_idle(page, 1000)
        page.get_by_text(name, exact=True).first.click()
        wait_idle(page, 800)
        page.wait_for_timeout(2500)

    def open_ebsd(name: str, wait_ms: int = 2000):
        files_sel(name)
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=900_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(wait_ms)

    def open_project():
        page.goto(base + "/")
        wait_idle(page, 800)
        mark(page, "home_open").click()
        mark(page, f"recent_{pname}").click()
        wait_idle(page, 1500)
    r.step("open project", open_project)

    def files_axes():
        files_sel("JU04_area1.ang")
        mark(page, "ms_axes_mode").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_map_axes_exact_angle_only_angle_field")
        mark(page, "ms_bd_mode").get_by_text("vector", exact=True).click()
        page.wait_for_timeout(600)
        r.shot("files_map_axes_exact_vector_only_xyz_fields")
        mark(page, "ms_bd_mode").get_by_text("angle", exact=True).click()
        page.wait_for_timeout(400)
        files_sel("JU04_area3.ang")
        mark(page, "ms_axes_mode").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("files_map_axes_approximate_L_S_T_bd_S")
        mark(page, "ms_axis_x").hover()
        page.wait_for_timeout(900)
        r.shot("files_map_axes_label_tooltip")
    r.step("Files: map axes", files_axes)

    def ebsd_area1():
        open_ebsd("JU04_area1.ang")
        r.shot("ebsd_map_tab_named_Map_axes_in_map_info")
        mark(page, "sec_cleanup").click()
        page.wait_for_timeout(600)
        mark(page, "sec_clean_grain_dilation").click()
        page.wait_for_timeout(600)
        r.shot("cleanup_grain_dilation_single_iteration_default_on")
        mark(page, "sec_clean_grain_ci").click()
        page.wait_for_timeout(600)
        r.shot("cleanup_grain_ci_standardization_panel")
        mark(page, "run_clean_grain_ci").click()
        page.wait_for_timeout(1500)
        wait_idle(page, 2000)
        mark(page, "clean_summary_grain_ci").scroll_into_view_if_needed()
        page.wait_for_timeout(1500)
        r.shot("cleanup_grain_ci_applied_summary_and_history")
        mark(page, "sec_cleanup").click()
        page.wait_for_timeout(500)
    r.step("EBSD JU04_area1: Map tab, cleanup", ebsd_area1)

    def texture_no_bd():
        mark(page, "sec_texture").click()
        page.wait_for_timeout(800)
        mark(page, "tex_dirs_unavailable").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        r.shot("texture_no_bd_map_axes_default_unavailable_greyed")
        mark(page, "dir_na_BD").first.hover()
        page.wait_for_timeout(1000)
        r.shot("texture_unavailable_BD_tooltip")
        mark(page, "run_texture").click()
        mark(page, "pf_image").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        mark(page, "rtab_ipf").click()
        page.wait_for_timeout(1500)
        r.shot("texture_ipf_along_x_y_z_without_bd")
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(1500)
        mark(page, "sec_texture").click()
        page.wait_for_timeout(400)
    r.step("Texture without BD (JU04_area1)", texture_no_bd)

    def grainsize_view():
        if only:                                           # run alone: open the map first
            open_ebsd("JU04_area1.ang")
        mark(page, "sec_grainsize").click()
        page.wait_for_timeout(800)
        mark(page, "grainsize_lognormal_fit").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        r.shot("grainsize_figure_settings_lognormal_off")
        inp(page, "grainsize_bin_um").fill("0.05")
        inp(page, "grainsize_min_um").fill("0.5")
        inp(page, "grainsize_min_um").press("Tab")
        mark(page, "run_gs_fig").click()
        page.wait_for_timeout(2500)
        wait_idle(page, 1500)
        img_loaded(page)
        r.shot("results_histogram_bins_only_no_history_step")
        mark(page, "grainsize_lognormal_fit").click()
        mark(page, "run_gs_fig").click()
        page.wait_for_timeout(2500)
        wait_idle(page, 1500)
        img_loaded(page)
        page.locator('[data-testid="result_card"]').last.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        r.shot("results_histogram_lognormal_fit_of_shown_grains")
        cards = page.locator('[data-testid="result_card"]')
        n0 = cards.count()
        cards.nth(n0 - 1).click(button="right")
        page.wait_for_timeout(600)
        mark(page, "result_delete").click()
        page.wait_for_timeout(1500)
        r.shot("results_figure_deleted_no_error")
        print(f"  result cards {n0} -> {cards.count()}")
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(1200)
    r.step("Grain-size figure as view (JU04_area1)", grainsize_view)

    def roi_tips():
        mark(page, "sec_grainsize").click()
        page.wait_for_timeout(400)
        mark(page, "sec_annotations").scroll_into_view_if_needed()
        page.get_by_text("Annotations and ROIs", exact=True).first.hover()
        page.wait_for_timeout(1000)
        r.shot("annotations_and_rois_hover_text")
        mark(page, "viewer_tool").hover()
        page.wait_for_timeout(1000)
        r.shot("tool_list_hover_text_roi")
        mark(page, "sec_texture").click()
        page.wait_for_timeout(600)
        choose(page, "texture_region", "ROI")
        page.wait_for_timeout(400)
        mark(page, "texture_roi_id").hover()
        page.wait_for_timeout(1000)
        r.shot("texture_roi_field_hover_text")
        choose(page, "texture_region", "whole map")
    r.step("ROI hover texts", roi_tips)

    def area3():
        open_ebsd("JU04_area3.ang", 4000)
        r.shot("ebsd_JU04_area3_alpha_only_Map_tab_axes_L_S_T")
        mark(page, "mtab_beta").click()
        page.wait_for_timeout(2500)
        wait_idle(page, 2000)
        page.wait_for_timeout(2500)
        r.shot("prior_beta_of_alpha_only_map_reconstructed")
        mark(page, "sec_recon").click()
        page.wait_for_timeout(800)
        r.shot("recon_section_alpha_only_map")
        mark(page, "run_recon").click()                    # the GUI path of bug 1 (failed before on this map)
        page.get_by_text("reconstruction done").first.wait_for(timeout=1_800_000)
        mark(page, "rtab_results").click()                 # the summary table is on the Results tab
        mark(page, "recon_summary").wait_for(timeout=120_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)
        r.shot("recon_summary_parent_phase_added")
        mark(page, "sec_recon").click()
        page.wait_for_timeout(500)
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(2500)
        wait_idle(page, 1500)
        mark(page, "sec_grainsize").click()
        page.wait_for_timeout(600)
        mark(page, "run_gs").click()
        mark(page, "gs_summary").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        r.shot("grainsize_summary_retained_beta_not_in_file")
    r.step("JU04_area3: alpha-only map, reconstruction, retained beta", area3)

    def area3_texture():
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(1200)
        mark(page, "sec_texture").click()
        page.wait_for_timeout(800)
        r.shot("texture_section_directions_L_S_T_BD")
        mark(page, "run_texture").click()
        mark(page, "pf_image").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        r.shot("pole_figures_axes_L_S_T")
        mark(page, "rtab_ipf").click()
        page.wait_for_timeout(1500)
        r.shot("inverse_pole_figures_L_S_T")
        mark(page, "mtab_alpha").click()
        page.wait_for_timeout(1500)
        mark(page, "sec_elastic").click()
        page.wait_for_timeout(600)
        mark(page, "run_elastic").click()
        mark(page, "elastic_image").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        r.shot("elastic_E_along_L_S_T")
    r.step("JU04_area3: texture / elastic named L S T", area3_texture)

    def settings():
        mark(page, "settings_gear").click()
        mark(page, "settings_phases").scroll_into_view_if_needed()
        page.wait_for_timeout(700)
        r.shot("settings_parent_beta_lattice")
        page.keyboard.press("Escape")
    r.step("Settings: parent beta phase", settings)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8107)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_fix671"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_fix671"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="keep the prepared project of an earlier run")
    ap.add_argument("--only", nargs="*", help="run only the steps whose name contains one of these words")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    if out.exists() and not a.only:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if not (a.reuse and (work / "projects" / "fix671_demo").is_dir()):
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        pname = prepare(work)
    else:
        pname = "fix671_demo"
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
            flow(r, page, base, pname, set(a.only) if a.only else None)
            br.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:  # noqa: BLE001
            srv.kill()
        log.close()
    errs = [ln for ln in (work / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()
            if "Traceback" in ln or "Error" in ln]
    print(f"{r.n if r else 0} screenshots in {out}; failed steps: {r.failed if r else '?'}; server errors: {len(errs)}")
    for e in errs[:20]:
        print("  ", e)
    return 1 if (r is None or r.failed) else 0


if __name__ == "__main__":
    sys.exit(main())
