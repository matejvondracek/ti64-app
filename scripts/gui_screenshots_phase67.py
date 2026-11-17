"""Screenshots of phase 6.7: EBSD tab sections 'Texture' and 'Elastic properties', result tabs 'Pole figures',
'Inverse pole figures' and 'Elastic' (full JU02 map: alpha and reconstructed prior beta; JU06 4PB maps: E along the
beam axis per sample), Export ODF, Settings 'Elastic constants'.

Usage:  python scripts/gui_screenshots_phase67.py [--out projects/_screens_phase67] [--port 8103] [--headed]
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

JU06 = REPO / "data" / "JU06" / "EBSD"


def prepare(work: Path) -> str:
    env_backup = dict(os.environ)
    os.environ["TI64_PROJECTS_DIR"] = str(work / "projects")
    os.environ["TI64_PREFS"] = str(work / "prefs" / "preferences.json")
    sys.path.insert(0, str(REPO / "python"))
    from ti64app import service
    from ti64app.project import Project
    from ti64app.sample import SampleInfo

    p = Project.create("phase67_demo", base=work / "projects")
    ju02 = sorted(DATA.glob("*JU02*.ang"))[0]
    t = time.time()
    for sid in ("JU02", "JU06"):
        p.save_sample_info(SampleInfo(id=sid, build_direction_specimen="+Y"))
    pos = {"map_x_axis": "+X", "map_y_axis": "-Y"}
    service.run(p, "sample.set", {"map_id": "JU02", "specimen_id": "JU02", "map_file": str(ju02),
                                  "input_state": "oim_cleaned", "build_direction": {"angle_deg": 270}, "position": pos})
    for m in ("compression", "tensile_mid", "tensile"):
        service.run(p, "sample.set", {"map_id": f"JU06_{m}", "specimen_id": "JU06",
                                      "map_file": str(JU06 / f"JU06_{m}.ang"), "input_state": "raw",
                                      "build_direction": {"angle_deg": 270}, "position": pos})
    p.update_file("JU02", indexing="spherical", indexing_asked=True)     # 'Rescan' export: answered (fix 5)
    service.run(p, "ebsd.load", {"map_id": "JU02"})
    service.run(p, "grains.calc", {"map_id": "JU02"})
    r = service.run(p, "recon.run", {"map_id": "JU02"})
    service.run(p, "grains.calc", {"map_id": "JU02", "source": r.id})
    for m in ("compression", "tensile_mid", "tensile"):
        service.run(p, "ebsd.load", {"map_id": f"JU06_{m}"})
        service.run(p, "ebsd.clean", {"map_id": f"JU06_{m}", "operation": "ci_threshold", "params": {}})
        service.run(p, "grains.calc", {"map_id": f"JU06_{m}"})
    print(f"  prepared in {time.time() - t:.0f} s")
    os.environ.clear()
    os.environ.update(env_backup)
    return p.root.name


def flow(r: Run, page, base: str, pname: str, mock: Path) -> None:
    def open_ebsd(map_name: str):
        page.goto(base + "/files")
        wait_idle(page, 1000)
        page.get_by_text(map_name, exact=True).first.click()
        wait_idle(page, 800)
        page.wait_for_timeout(9000)                    # the map sheet of a 2.6 M-point map takes a few seconds
        mark(page, "open_tool").click()
        mark(page, "map_viewer").wait_for(timeout=600_000)
        wait_idle(page, 2000)
        page.wait_for_timeout(1500)

    def open_project():
        page.goto(base + "/")
        wait_idle(page, 800)
        mark(page, "home_open").click()
        mark(page, f"recent_{pname}").click()
        wait_idle(page, 1500)
    r.step("open project", open_project)

    def texture_alpha():
        open_ebsd(next(f.name for f in DATA.glob("*JU02*.ang")))
        mark(page, "sec_texture").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_texture_section")
        mark(page, "run_texture").click()
        mark(page, "pf_image").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        r.shot("tab_pole_figures_alpha_ju02")
        mark(page, "rtab_ipf").click()
        page.wait_for_timeout(1500)
        r.shot("tab_inverse_pole_figures_alpha_ju02")
    r.step("Texture α (JU02)", texture_alpha)

    def texture_beta():
        choose(page, "texture_phase", "prior β (reconstruction)")
        choose(page, "texture_weighting", "one point per grain")
        page.wait_for_timeout(500)
        r.shot("ebsd_texture_prior_beta_changed_not_applied")
        mark(page, "run_texture").click()
        page.wait_for_timeout(3000)
        wait_idle(page, 2000)
        mark(page, "pf_image").wait_for(timeout=900_000)
        page.wait_for_timeout(2000)
        r.shot("tab_pole_figures_prior_beta_few_grains_warning")
    r.step("Texture prior β (JU02)", texture_beta)

    def export_odf():
        r.queue(str(Path(mock).parent / "JU02_prior_beta_odf.txt"))
        mark(page, "export_odf_tab").click()
        page.wait_for_timeout(3000)
        r.shot("export_odf_notification")
    r.step("Export ODF", export_odf)

    def elastic():
        mark(page, "sec_elastic").click()
        page.wait_for_timeout(800)
        r.shot("ebsd_elastic_section")
        mark(page, "run_elastic").click()
        mark(page, "elastic_image").wait_for(timeout=900_000)
        wait_idle(page, 1500)
        page.wait_for_timeout(1500)
        r.shot("tab_elastic_ju02")
        mark(page, "elastic_matrices").click()
        page.wait_for_timeout(800)
        mark(page, "sample_E_table").scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        r.shot("tab_elastic_matrices_and_sample_table")
    r.step("Elastic (JU02)", elastic)

    def sample_table():
        open_ebsd("JU06_compression.ang")
        mark(page, "rtab_elastic").click()
        page.wait_for_timeout(1000)
        r.shot("tab_elastic_ju06_sample_table_empty")
        mark(page, "elastic_all_maps").click()
        page.wait_for_timeout(5000)
        wait_idle(page, 3000)
        mark(page, "elastic_image").wait_for(timeout=900_000)
        page.wait_for_timeout(2000)
        mark(page, "sample_E_table").scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        r.shot("tab_elastic_ju06_E_beam_axis_per_map")
    r.step("Per-sample E table (JU06)", sample_table)

    def settings():
        mark(page, "settings_gear").click()
        mark(page, "settings_elastic").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        page.locator('[data-testid="elastic_preset"]').first.click()
        page.wait_for_timeout(700)
        r.shot("settings_elastic_constants")
        page.keyboard.press("Escape")
    r.step("Settings: elastic constants", settings)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8103)
    ap.add_argument("--out", default=str(REPO / "projects" / "_screens_phase67"))
    ap.add_argument("--work", default=str(REPO / "projects" / "_gui_demo_phase67"))
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
            flow(r, page, base, pname, mock)
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
    (out / "summary.json").write_text(json.dumps({"failed": r.failed, "logs": r.logs[-50:]}, indent=1))
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
