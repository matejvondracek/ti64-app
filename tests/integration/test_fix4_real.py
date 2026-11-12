"""Phase 6.6 fix 4 on the real data (slow; data/JU04, data/JU06 are git-ignored, tests skip without them):
APEX phase names in both backends, PRIAS exports, hex .up2 with padding patterns + scan-ID pairing."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ti64app.io import prias, up2

pytestmark = pytest.mark.slow
REPO = Path(__file__).resolve().parents[2]
NEW = sorted((REPO / "data" / "JU04").glob("*.ang")) + sorted((REPO / "data" / "JU06").glob("*.ang"))
SMALL = [f for f in NEW if f.stat().st_size < 40e6]          # JU04_area1/2, JU06_* (area3/4 only in the script run)
UP2 = {"Area 1": "JU04_area1.ang", "Area 2": "JU04_area2.ang"}


def _need(files):
    if not files:
        pytest.skip("data/JU04 / data/JU06 missing (git-ignored)")


@pytest.mark.parametrize("backend", ["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
@pytest.mark.parametrize("ang", SMALL, ids=[f.stem for f in SMALL])
def test_apex_ang_loads_with_canonical_phases(ang, backend, project):
    """'Ti (Titanium, alpha/beta)' -> 'Ti (alpha)' / 'Ti (beta)' by symmetry; single-phase maps are phase 1 (MATLAB
    used to return phase 0 = not indexed); raw state suggested from the name."""
    from ti64app import service
    from ti64app.backends import get_backend
    from ti64app.sample import suggest_input_state

    be = get_backend(backend)
    if not be.is_available():
        pytest.skip(be.unavailable_reason())
    assert suggest_input_state(ang) == "raw"
    project.set_backend(backend)
    service.run(project, "sample.set", {"map_id": ang.stem, "map_file": str(ang), "input_state": "raw"})
    s = service.run(project, "ebsd.load", {"map_id": ang.stem})
    meta = json.loads((project.root / s.outputs["meta"].path).read_text(encoding="utf-8"))
    with np.load(project.root / s.outputs["ebsd"].path) as z:
        ph = z["phase"]
    names = meta["phase_names"]
    assert names["1"] == "Ti (alpha)" and set(names.values()) <= {"Ti (alpha)", "Ti (beta)"}
    assert {x["original"] for x in meta["phases"]} <= {"Ti (Titanium, alpha)", "Ti (Titanium, beta)"}
    assert (ph == 1).mean() > 0.5                         # alpha is indexed, not 'not indexed'
    if "Ti (beta)" in names.values():
        assert (ph == 2).any()
    s = service.run(project, "figure", {"map_id": ang.stem, "kind": "ipf", "direction": "Z"})
    assert (project.root / s.outputs["figure"].path).stat().st_size > 1000


@pytest.mark.matlab
def test_phase_numbers_same_in_both_backends(project):
    """Per-pixel phase numbers of a single-phase and a two-phase APEX map agree between pymtex and MATLAB."""
    from ti64app.backends import get_backend
    from ti64app.backends.base import LoadParams
    from ti64app.phases import for_file, load_names

    files = [f for f in SMALL if f.stem in ("JU04_area2", "JU06_tensile")]
    _need(files)
    m = get_backend("matlab")
    if not m.is_available():
        pytest.skip(m.unavailable_reason())
    for f in files:
        lp = LoadParams(file=f, input_state="raw", phase_names=load_names(for_file(f)))
        a = get_backend("pymtex").get_ebsd(get_backend("pymtex").load_ebsd(lp))
        b = m.get_ebsd(m.load_ebsd(lp))
        assert a.phase_names == b.phase_names
        np.testing.assert_array_equal(a.phase, b.phase)


@pytest.mark.parametrize("ang", NEW, ids=[f.stem for f in NEW])
def test_prias_exports_match_their_maps(ang):
    files = prias.find_files(ang)
    if not files:
        pytest.skip(f"no PRIAS export for {ang.name}")
    for f in files:
        c = prias.check(f, ang)
        assert c["ok"], c["message"]
        assert c["key"] == "top"


@pytest.mark.parametrize("area", sorted(UP2))
def test_up2_hex_padding_and_pairing(area, tmp_path):
    """Scan ID pairs the .up2 with its .ang; the overview (every 2nd row / column) correlates with the .ang IQ at
    zero shift (after removing the smooth shading); the old reader (padding counted as points) gives r ~ 0."""
    import dataclasses

    from ti64app.modules import up2_overview as uo
    from ti64app.modules import up2_pair as pr

    f = next(iter((REPO / "data" / "JU04").glob(f"*{area}*.up2")), None)
    _need([f] if f else [])
    h = up2.read_header(f)
    assert h.hex_grid and h.extra_patterns and h.size_ok and h.n_patterns == h.n_rows * h.n_cols
    pair = pr.paired(None, f)
    assert pair is not None and pair.ang.name == UP2[area]
    from ti64app.io.ang import read_header

    assert read_header(pair.ang).n_points == h.n_points
    ov = uo.compute(f, tmp_path, 2)
    c = pr.compare(ov, pair)
    assert c["shift"] == (0, 0) and c["r"] > 0.3, c
    bad = dataclasses.replace(h, extra_patterns=False)        # the old reader: n_cols - 1 patterns per short row
    orig = up2.read_header
    try:
        up2.read_header = lambda p: bad
        ov_old = uo.compute(f, tmp_path / "old", 2)
    finally:
        up2.read_header = orig
    assert abs(pr.compare(ov_old, pair)["r"]) < 0.1


def test_up2_padding_at_end_full_resolution(tmp_path):
    """Full resolution, JU04 Area 2: odd (short) rows correlate as well as even rows with the padding at the end;
    with the padding at the start they drop (module doc of io/up2.py)."""
    from ti64app.modules import up2_overview as uo
    from ti64app.modules import up2_pair as pr

    f = next(iter((REPO / "data" / "JU04").glob("*Area 2*.up2")), None)
    _need([f] if f else [])
    pair = pr.paired(None, f)
    c = pr.compare(uo.compute(f, tmp_path, 1), pair)
    assert c["odd"] > c["even"] - 0.03, c
    try:
        up2.PAD_AT_END = False
        c2 = pr.compare(uo.compute(f, tmp_path / "start", 1), pair)
    finally:
        up2.PAD_AT_END = True
    assert c2["odd"] < c["odd"] - 0.1, (c, c2)
