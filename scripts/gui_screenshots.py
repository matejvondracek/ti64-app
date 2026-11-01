"""Click through every GUI page and panel with Playwright and save screenshots (phase 5 visual check).

Usage:  python scripts/gui_screenshots.py [--project phase5_demo] [--port 8091] [--out projects/<project>/screenshots]
        [--headed]
Needs a project with a loaded map, grains, KAM and a reconstruction (scripts/make_demo_project.py) and Playwright
(`pip install playwright` + `python -m playwright install chromium`). Starts the GUI itself and stops it at the end.
Every step that fails is reported and the run continues; the exit code is the number of failed steps.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class Run:
    def __init__(self, page, out: Path) -> None:
        self.page, self.out, self.n, self.failed = page, out, 0, []
        self.logs: list[str] = []
        page.on("console", lambda m: m.type == "error" and self.logs.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: self.logs.append(f"pageerror: {e}"))

    def shot(self, name: str, el=None) -> None:
        self.n += 1
        f = self.out / f"{self.n:02d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.png"
        (el or self.page).screenshot(path=str(f))
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


def mark(page, name: str):
    return page.locator(f'[data-testid="{name}"]').first


def wait_idle(page, ms: int = 600) -> None:
    page.wait_for_timeout(ms)
    # notifications with a spinner disappear when the backend call is done
    for _ in range(600):
        if page.locator(".q-notification .q-spinner").count() == 0:
            break
        page.wait_for_timeout(200)
    page.wait_for_timeout(300)


def tool(page, name: str) -> None:
    """Select a viewer tool and wait until the browser has it (the choice goes through the server)."""
    page.locator('[data-testid="viewer_tool"] button', has_text=name).first.click()
    page.wait_for_timeout(700)


def choose(page, testid: str, option: str) -> None:
    """Pick an option of a NiceGUI (Quasar) select."""
    mark(page, testid).click()
    page.locator(".q-menu .q-item", has_text=option).first.click()
    page.wait_for_timeout(300)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="phase5_demo")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--out", default=None)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    from ti64app.project import projects_dir  # noqa: PLC0415

    out = Path(a.out) if a.out else projects_dir() / a.project / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.png"):
        f.unlink()
    env = {**os.environ, "PYTHONPATH": str(REPO / "python")}
    log = open(out / "server.log", "w", encoding="utf-8")
    srv = subprocess.Popen([sys.executable, "-m", "ti64app.cli", "gui", "--port", str(a.port), "--project", a.project],
                           cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{a.port}"
    try:
        for _ in range(240):
            if srv.poll() is not None:
                break
            try:
                urllib.request.urlopen(url, timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        if srv.poll() is not None:
            log.flush()
            print("GUI server exited:\n" + (out / "server.log").read_text(encoding="utf-8")[-3000:])
            return 99
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=not a.headed)
            page = b.new_page(viewport={"width": 1600, "height": 1000})
            r = Run(page, out)
            clickthrough(r, page, url)
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
    print(f"\n{r.n} screenshots in {out}")
    for line in r.logs:
        print("  browser", line)
    for f in r.failed:
        print("  FAILED", f)
    if r.failed:
        print("server log (tail):\n" + (out / "server.log").read_text(encoding="utf-8")[-3000:])
    return len(r.failed)


def clickthrough(r: Run, page, url: str) -> None:
    def projects():
        page.goto(url + "/")
        page.wait_for_selector("text=Projects in")
        wait_idle(page)
        r.shot("projects")
    r.step("project browser", projects)

    def viewer_open():
        page.goto(url + "/viewer")
        page.wait_for_selector(".leaflet-tile-loaded", timeout=60000)
        wait_idle(page, 1500)
        r.shot("viewer_ipf")
    r.step("viewer: IPF map", viewer_open)

    def overlays():
        for o in ("ov_hagb", "ov_beta_gb", "ov_crack"):
            mark(page, o).click()
            wait_idle(page, 800)
        page.wait_for_timeout(1500)
        r.shot("viewer_overlays_hagb_betagb_crack")
        r.shot("panel_overlays", mark(page, "panel_overlays"))
        mark(page, "ov_lagb").click()
        mark(page, "ov_hagb").click()
        wait_idle(page, 1500)
        r.shot("viewer_overlay_lagb")
        for o in ("ov_lagb", "ov_beta_gb"):
            mark(page, o).click()
        wait_idle(page, 800)
    r.step("viewer: overlays", overlays)

    def map_types():
        for opt, name in (("KAM", "kam"), ("phase", "phase"), ("CI", "ci"), ("prior β IPF", "parent_ipf"),
                          ("α variant ID", "variant"), ("reconstruction fit", "recon_fit")):
            choose(page, "viewer_kind", opt)
            wait_idle(page, 1500)
            page.wait_for_selector(".leaflet-tile-loaded", timeout=60000)
            page.wait_for_timeout(1200)
            r.shot(f"viewer_{name}")
        r.shot("panel_key_recon_fit", mark(page, "panel_key"))
        choose(page, "viewer_kind", "IPF (alpha / beta)")
        wait_idle(page, 1500)
    r.step("viewer: map types", map_types)

    def ipf_direction():
        choose(page, "ipf_dir_alpha", "X")
        choose(page, "ipf_dir_beta", "X")
        mark(page, "viewer_apply").click()
        wait_idle(page, 2000)
        r.shot("viewer_ipf_x")
        r.shot("panel_layer", mark(page, "panel_layer"))
        mark(page, "viewer_ipfkey").click()
        wait_idle(page, 1500)
        page.wait_for_selector('[data-testid="viewer_keyimg"]', timeout=60000)
        page.wait_for_timeout(800)
        r.shot("panel_key_ipf_key", mark(page, "panel_key"))
        choose(page, "ipf_dir_alpha", "Z")
        choose(page, "ipf_dir_beta", "Z")
        mark(page, "viewer_apply").click()
        wait_idle(page, 1500)
    r.step("viewer: IPF direction and key", ipf_direction)

    def zoom():
        m = mark(page, "map_viewer")
        box = m.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.45)
        for _ in range(3):
            page.mouse.wheel(0, -240)
            page.wait_for_timeout(500)
        page.wait_for_timeout(2500)
        r.shot("viewer_zoom_in")
        page.mouse.move(box["x"] + 300, box["y"] + 300)
        page.mouse.down()
        page.mouse.move(box["x"] + 500, box["y"] + 400, steps=8)
        page.mouse.up()
        page.wait_for_timeout(2000)
        r.shot("viewer_pan")
        for _ in range(3):
            page.mouse.wheel(0, 240)
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
    r.step("viewer: zoom and pan", zoom)

    def tools():
        m = mark(page, "map_viewer")
        box = m.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        tool(page, "probe")
        page.mouse.click(cx, cy)
        page.wait_for_timeout(800)
        r.shot("viewer_probe", mark(page, "viewer_status"))
        tool(page, "measure")
        page.mouse.click(cx - 150, cy - 100)
        page.mouse.click(cx + 100, cy + 50)
        page.wait_for_timeout(800)
        r.shot("viewer_measure", mark(page, "viewer_status"))
        tool(page, "arrow")
        page.mouse.click(cx + 200, cy - 200)
        page.mouse.click(cx + 120, cy - 120)
        wait_idle(page, 1200)
        mark(page, "ann_text").fill("pore")
        tool(page, "text")
        page.mouse.click(cx + 210, cy - 215)
        wait_idle(page, 1200)
        mark(page, "ann_text").fill("R2")
        tool(page, "ROI ▭")
        page.mouse.click(cx - 300, cy - 250)
        page.mouse.click(cx - 120, cy - 100)
        wait_idle(page, 1200)
        tool(page, "pan / zoom")
        page.wait_for_timeout(1000)
        r.shot("viewer_annotations")
        r.shot("panel_tools", mark(page, "panel_tools"))
    r.step("viewer: tools (probe, measure, arrow, text, ROI)", tools)

    def export():
        page.locator(".q-expansion-item", has_text="Export").locator(".q-item").first.click()
        page.wait_for_timeout(500)
        choose(page, "exp_mode", "4× nearest neighbour")
        choose(page, "exp_region", "ROI R2")
        mark(page, "run_export").click()
        wait_idle(page, 1500)
        page.wait_for_selector('[data-testid="exp_result"]', timeout=120000)
        r.shot("panel_export_4x_roi", mark(page, "panel_export"))
        choose(page, "exp_mode", "width in mm at DPI (figure)")
        choose(page, "exp_fmt", "pdf")
        choose(page, "exp_region", "whole map")
        mark(page, "run_export").click()
        wait_idle(page, 3000)
        page.wait_for_timeout(1000)
        r.shot("panel_export_width_pdf", mark(page, "panel_export"))
    r.step("viewer: export", export)

    def crop():
        page.locator('[data-testid="ann_list"] button').filter(has=page.locator("text=content_cut")).first.click()
        page.wait_for_timeout(800)
        r.shot("viewer_crop_dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    r.step("viewer: crop dialog", crop)

    for path, name, wait in (("/sample", "sample_sheet", "Map file"), ("/maps", "maps", "1 Cleanup"),
                             ("/recon", "reconstruction", "1 Reconstruct"), ("/grainsize", "grain_size", "1 Alpha grains")):
        def pg(path=path, name=name, wait=wait):
            page.goto(url + path)
            page.wait_for_selector(f"text={wait}")
            wait_idle(page)
            for _ in range(10):                      # open every closed panel (re-query after each click)
                closed = page.locator(".q-expansion-item--collapsed > div > .q-item")
                if closed.count() == 0:
                    break
                closed.first.click()
                page.wait_for_timeout(300)
            page.wait_for_timeout(500)
            r.shot(name)
        r.step(f"page {path}", pg)

    def maps_figure():
        page.goto(url + "/maps")
        page.wait_for_selector("text=4 Figure")
        mark(page, "run_figure").click()
        wait_idle(page, 2000)
        page.wait_for_selector(".q-img__image--loaded", state="attached", timeout=120000)
        page.wait_for_timeout(1000)
        r.shot("maps_figure_ipf")
    r.step("maps: draw a figure", maps_figure)

    def ops():
        page.goto(url + "/ops")
        page.wait_for_selector("text=Parameter panels")
        wait_idle(page)
        r.shot("ops_cleanup_panel")
        for op in ("export.map", "recon.run", "grains.calc"):
            choose(page, "ops_select", op)
            page.wait_for_timeout(600)
            r.shot(f"ops_{op.replace('.', '_')}_panel")
        mark(page, "ops_run").click()
        wait_idle(page, 2000)
        page.wait_for_selector('[data-testid="ops_result"]', timeout=120000)
        r.shot("ops_grains_result")
    r.step("operations: parameter panels", ops)

    def recipe():
        page.goto(url + "/recipe")
        page.wait_for_selector("text=Recipe")
        wait_idle(page)
        r.shot("recipe")
        page.locator('[data-testid="recipe_table"] tbody tr').nth(1).locator("td").first.click()
        page.wait_for_timeout(1000)
        r.shot("recipe_step_detail")
        mark(page, "undo").click()
        wait_idle(page, 1500)
        r.shot("recipe_after_undo")
        mark(page, "redo").click()
        wait_idle(page, 1500)
        r.shot("recipe_after_redo")
        mark(page, "replay").click()
        page.wait_for_selector('[data-testid="replay_result"]', timeout=600000)
        wait_idle(page, 500)
        r.shot("recipe_replay")
    r.step("recipe: details, undo, redo, replay", recipe)

    def settings():
        page.goto(url + "/settings")
        page.wait_for_selector("text=Analysis backend")
        wait_idle(page)
        r.shot("settings")
        for c in ("settings_backend", "settings_ipf", "settings_style", "settings_paths"):
            r.shot(c, mark(page, c))
    r.step("settings", settings)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "python"))
    sys.exit(main())
