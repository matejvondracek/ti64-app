"""Browser click-through of every GUI page and panel (scripts/gui_screenshots.py) on a demo project built from the
JU02 1000x1000 crop (load, grains, KAM, reconstruction, annotations). Needs Playwright + Chromium:
    pip install playwright && python -m playwright install chromium
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.slow


def test_click_through_every_panel(tmp_path):
    pytest.importorskip("playwright")
    from tests.conftest import CROPS

    if not sorted(CROPS.glob("*JU02*_crop1000.ang")):
        pytest.skip("crops missing (python scripts/make_crops.py)")
    # projects dir + prefs: isolated by conftest; no PYTEST_* variables, or NiceGUI starts in its own test mode
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["PYTHONPATH"] = str(REPO / "python")
    subprocess.run([sys.executable, str(REPO / "scripts" / "make_demo_project.py"), "--name", "pw_demo"],
                   cwd=REPO, env=env, check=True, capture_output=True, timeout=900)
    out = tmp_path / "shots"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gui_screenshots.py"), "--project", "pw_demo",
                        "--port", "8093", "--out", str(out)], cwd=REPO, env=env, capture_output=True, text=True,
                       timeout=1800)
    shots = sorted(out.glob("*.png"))
    assert r.returncode == 0, r.stdout[-4000:]
    assert len(shots) >= 40 and not any("FAILED" in f.name for f in shots)
    assert "pageerror" not in r.stdout
