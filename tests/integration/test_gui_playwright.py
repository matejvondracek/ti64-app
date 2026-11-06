"""Browser click-through of the GUI (scripts/gui_screenshots.py, --browser mode, Windows dialogs mocked):
home -> new project -> add files -> find missing -> new sample for a file -> fold -> View tab (image, .up2) ->
EBSD load / cleanup / grains -> overlay menu -> legends on the map -> hexagons when zoomed -> export dialog (sizes) ->
history reorder (refused + applied + undo) -> prior-beta map tab -> Settings -> Stitch (stitch sets, greyed info-strip
option, Arrange, Refine, overlap difference, export, View) -> Crack, then 1920x1080. Needs the test data and
Playwright + Chromium:
    pip install playwright && python -m playwright install chromium
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.slow


def test_click_through_new_layout(tmp_path):
    pytest.importorskip("playwright")
    from tests.conftest import CROPS, DATA

    if not sorted(CROPS.glob("*JU02*_crop1000.ang")) or not (DATA / "stitch").is_dir():
        pytest.skip("test data missing (crops: python scripts/make_crops.py; data/test/stitch)")
    # no PYTEST_* variables, or the GUI would start in NiceGUI's test mode
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["PYTHONPATH"] = str(REPO / "python")
    out = tmp_path / "shots"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gui_screenshots.py"), "--port", "8093",
                        "--out", str(out), "--work", str(tmp_path / "work")], cwd=REPO, env=env, capture_output=True,
                       text=True, timeout=2400)
    shots = sorted(out.glob("*.png"))
    assert r.returncode == 0, r.stdout[-4000:]
    assert len(shots) >= 35 and not any("FAILED" in f.name for f in shots)
    assert "pageerror" not in r.stdout
    assert (tmp_path / "work" / "exports" / "JU02_ipf_export.png").is_file()
    assert (tmp_path / "work" / "exports" / "JU05_mosaic.png").is_file()
