"""Phase 6.6 fix 4 (unit level, synthetic files): phases by crystal symmetry, PRIAS text exports, hex .up2 with padding
patterns and scan-ID pairing, Claude reports, breadcrumbs."""
from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path

import numpy as np
import pytest

from ti64app import phases
from ti64app.io import prias, up2


# ---- phases by symmetry ------------------------------------------------------------------------------------------
def _ph(i, name, sym):
    return {"id": i, "MaterialName": name, "Symmetry": str(sym)}


@pytest.mark.parametrize("names", [("Ti (alpha)", "Ti (beta)"), ("Ti (Titanium, alpha)", "Ti (Titanium, beta)"),
                                   ("phase A", "phase B")])
def test_phases_by_symmetry_not_name(names):
    got, problems = phases.resolve([_ph(2, names[1], 43), _ph(1, names[0], 62)])
    assert not problems
    assert {p.id: p.name for p in got} == {1: phases.ALPHA, 2: phases.BETA}
    assert {p.id: p.original for p in got} == {1: names[0], 2: names[1]}


def test_phases_swapped_ids_and_single_phase():
    got, _ = phases.resolve([_ph(1, "Ti (Titanium, beta)", 43), _ph(2, "Ti (Titanium, alpha)", 62)])
    assert phases.load_names(got) == {1: phases.BETA, 2: phases.ALPHA}
    got, problems = phases.resolve([_ph(1, "Ti (Titanium, alpha)", 62)])
    assert not problems and phases.load_names(got) == {1: phases.ALPHA}


def test_phases_ambiguous_until_user_decides():
    hp = [_ph(1, "Ti (alpha)", 62), _ph(2, "Ti3Al", 62)]
    _, problems = phases.resolve(hp)
    assert problems and "2 phases would be α" in problems[0]
    got, problems = phases.resolve(hp, {"2": "other"})
    assert not problems
    assert phases.load_names(got) == {1: phases.ALPHA, 2: "Ti3Al"}
    assert [p.source for p in got] == ["symmetry", "map sheet"]


def test_ambiguous_error_names_the_map_sheet(tmp_path):
    f = tmp_path / "two_hex.ang"
    f.write_text("# Phase 1\n# MaterialName  \tA\n# Symmetry              62\n# Phase 2\n# MaterialName  \tB\n"
                 "# Symmetry              62\n# GRID: SqrGrid\n0 0 0 0 0 1 1 1 0 0\n", encoding="latin-1")
    from ti64app.sample import SheetIncomplete

    with pytest.raises(SheetIncomplete) as e:
        phases.for_file(f, None, "M1")
    assert "Files tab" in str(e.value) and "'Phases'" in str(e.value) and e.value.map_id == "M1"


def test_phase_map_in_sheet_marks_map_stale():
    from ti64app.recipe import RecipeStep
    from ti64app.sample import stale_on_sheet_change

    pred = stale_on_sheet_change("M", {"map_file": "a", "phase_map": None}, {"map_file": "a", "phase_map": {"2": "beta"}})
    assert pred(RecipeStep(op="ebsd.load", params={"map_id": "M"}))


# ---- PRIAS ------------------------------------------------------------------------------------------------------
def _hex_ang(path: Path, nrows=4, nodd=3, dx=0.2):
    dy = dx * np.sqrt(3) / 2
    ne = nodd - 1
    lines = ["# Phase 1", "# MaterialName  \tTi (Titanium, alpha)", "# Symmetry              62",
             "# GRID: HexGrid", f"# XSTEP: {dx:.6f}", f"# YSTEP: {dy:.6f}", f"# NCOLS_ODD: {nodd}",
             f"# NCOLS_EVEN: {ne}", f"# NROWS: {nrows}", "# SCANID: \t" + "12345678-9abc-def0-1122-334455667788"]
    xy = []
    for r in range(nrows):
        for c in range(nodd if r % 2 == 0 else ne):
            xy.append(((c + (0.5 if r % 2 else 0)) * dx, r * dy))
    rows = [f"0 0 0 {x:.5f} {y:.5f} 100 0.5 0 0 180" for x, y in xy]
    path.write_text("\n".join(lines + rows) + "\n", encoding="latin-1")
    return np.array(xy)


def _prias_file(path: Path, xy, vals, channel="PRIAS Top Strip"):
    head = ["# Header: Project1::X::All data::Auto PRIAS Top", "#", f"# Column 1: x", "# Column 2: y",
            f"# Column 3: Grayscale value - {channel}", "# Column 4: Color coded range - <none>", "#"]
    rows = [f"{x:.3f} {y:.3f} {v:g} 0 0" for (x, y), v in zip(xy, vals)]
    path.write_bytes(("\r\n".join(head + rows) + "\r\n").encode("latin-1"))


def test_prias_detect_match_and_channel(tmp_path):
    a = tmp_path / "M_area1.ang"
    xy = _hex_ang(a)
    order = np.random.default_rng(0).permutation(len(xy))      # export order does not matter: matched by x, y
    f = tmp_path / "M_area1_prias.txt.txt"
    _prias_file(f, xy[order], order.astype(float))
    assert prias.find_files(a) == [f] and prias.ang_of(f) == a and prias.channel(f) == ("top", "PRIAS Top Strip")
    v = prias.values(f, a, tmp_path / "cache")
    np.testing.assert_array_equal(v, np.arange(len(xy), dtype=float))
    assert prias.check(f, a)["ok"]


def test_prias_mismatch_messages(tmp_path):
    a = tmp_path / "M.ang"
    xy = _hex_ang(a)
    f = tmp_path / "M_prias.txt"
    _prias_file(f, xy[:-2], np.ones(len(xy) - 2))
    c = prias.check(f, a)
    assert not c["ok"] and "points" in c["message"] and "'All data'" in c["message"]
    _prias_file(f, xy + [5.0, 0.0], np.ones(len(xy)))
    c = prias.check(f, a)
    assert not c["ok"] and "not on the grid" in c["message"]


def test_prias_file_is_a_sidecar(tmp_path):
    from ti64app.sample import file_kind

    a = tmp_path / "M.ang"
    _hex_ang(a)
    f = tmp_path / "M_prias.txt"
    f.write_text("# x\n", encoding="latin-1")
    assert file_kind(f) is None
    assert file_kind(tmp_path / "other_prias.txt") == "other"      # no .ang of that name next to it


# ---- hex .up2 with padding + scan ID ------------------------------------------------------------------------------
GUID = "f4de6ea8-d6b4-4697-aea3-3f2acb4fe1ba"


def _write_hex_up2(path: Path, n_cols=4, n_rows=5, w=3, h=2, extra=True, guid=GUID):
    n_store = n_cols * n_rows if extra else (n_rows + 1) // 2 * n_cols + n_rows // 2 * (n_cols - 1)
    hdr = struct.pack("<4i", 4, w, h, 58) + struct.pack("<BiiBdd", int(extra), n_cols, n_rows, 1, 0.2, 0.2 * 3 ** 0.5 / 2)
    hdr += uuid.UUID(guid).bytes_le
    pats = np.zeros((n_store, h, w), "<u2")
    k = 0
    point = 0
    for r in range(n_rows):
        n = n_cols - (r % 2)
        for c in range(n):
            pats[k] = point                        # pattern value = its point index
            k += 1
            point += 1
        if extra and r % 2:
            pats[k] = 65535                        # padding pattern at the END of the short row
            k += 1
    path.write_bytes(hdr + pats.tobytes())
    return point


def test_up2_hex_padding_skipped(tmp_path):
    f = tmp_path / "a.up2"
    n_points = _write_hex_up2(f)
    hd = up2.read_header(f)
    assert hd.extra_patterns and hd.padded and hd.size_ok
    assert (hd.n_points, hd.n_patterns, hd.n_padding) == (n_points, 20, 2)
    assert hd.scan_id == GUID
    for r in range(hd.n_rows):
        row = up2.read_row(f, r, hd)
        assert row.shape[0] == up2.row_length(hd, r) and 65535 not in row
    pts = up2.read_points(f, 0, n_points, hd)
    np.testing.assert_array_equal(pts[:, 0, 0], np.arange(n_points))
    np.testing.assert_array_equal(np.concatenate([p for _, p in up2.iter_point_chunks(f, 4, hd)])[:, 0, 0],
                                  np.arange(n_points))
    assert up2.read_pattern(f, 3, 2, hd)[0, 0] == 4 + 3 + 4 + 2       # rows 0..2 = 4 + 3 + 4 points
    r, c = up2.point_rc(hd, np.arange(n_points))
    assert [up2.read_pattern(f, int(a), int(b), hd)[0, 0] for a, b in zip(r, c)] == list(range(n_points))


def test_up2_hex_without_padding_unchanged(tmp_path):
    f = tmp_path / "b.up2"
    n_points = _write_hex_up2(f, extra=False)
    hd = up2.read_header(f)
    assert not hd.padded and hd.n_patterns == n_points and hd.size_ok
    np.testing.assert_array_equal(up2.read_points(f, 0, n_points, hd)[:, 0, 0], np.arange(n_points))


def test_up2_paired_by_scan_id(tmp_path):
    f = tmp_path / "Area 1.up2"
    _write_hex_up2(f)
    good = tmp_path / "JU04_area1.ang"
    good.write_text(f"# SCANID: \t{GUID}\n0 0 0 0 0 1 1 1 0 0\n", encoding="latin-1")
    (tmp_path / "other.ang").write_text("# SCANID: \t00000000-0000-0000-0000-000000000001\n", encoding="latin-1")
    assert up2.find_paired_ang(f) == good


def test_overview_hex_subsampling_keeps_hex_grid(tmp_path):
    from ti64app.modules import up2_overview as uo

    f = tmp_path / "c.up2"
    _write_hex_up2(f, n_cols=9, n_rows=9, w=8, h=8)
    hd = up2.read_header(f)
    ov = uo.Overview(np.zeros((5, 5), np.float32), np.zeros((5, 5), np.float32), 2, 5, 0.0, hd)
    r, c, x, y = ov.points()
    # kept rows 0, 2, 4, ...: every other kept row starts one point (= step / 2) later -> hex grid of pitch 2 dx
    assert set(np.round(x[r == 0] / hd.x_step_um).astype(int)) == {0, 2, 4, 6, 8}
    assert set(np.round(x[r == 1] / hd.x_step_um).astype(int)) == {1, 3, 5, 7}
    idx = ov.point_index()
    assert idx[1, 0] == 9 + 8 + 1                       # scan row 2 starts after 9 + 8 points; column 1
    L = uo.layer(ov, np.zeros((5, 5), np.uint8), "k").layout
    assert L.hex and L.dx == pytest.approx(2 * hd.x_step_um)


# ---- report for Claude + breadcrumbs ---------------------------------------------------------------------------
def test_breadcrumbs_and_errors():
    from ti64app import breadcrumbs as bc

    bc.clear()
    for i in range(40):
        bc.add("click", f"button {i}")
    last = bc.last(30)
    assert len(last) == 30 and last[-1]["text"] == "button 39" and "time" in last[0]
    try:
        raise ValueError(r"bad file C:\Users\someone\data\x.ang")
    except ValueError as e:
        bc.record_error("test", e)
    errs = bc.errors()
    assert errs and "Traceback" in errs[-1]["traceback"] and "someone" not in errs[-1]["traceback"]


def test_report_for_claude(tmp_path, project):
    from ti64app import breadcrumbs as bc
    from ti64app import report

    bc.clear()
    bc.add("tab", "/ebsd")
    md, js = report.save_report("The IPF map looks wrong on JU04_area1.", "bug",
                                context={"tab": "/ebsd", "file": r"C:\Users\someone\data\JU04_area1.ang"},
                                project=project, folder=tmp_path, screenshot_png=b"\x89PNG fake")
    assert md.name.endswith("_bug.md") and js.with_suffix(".md") == md
    text = md.read_text(encoding="utf-8")
    order = ["The IPF map looks wrong", "## App", "## Backend", "## System", "## Current state",
             "## Last UI actions", "## Recipe", "## Errors since start", "## Log", "## Settings"]
    pos = [text.index(k) for k in order]
    assert pos == sorted(pos)
    assert "someone" not in text and "JU04_area1.ang" in text
    d = json.loads(js.read_text(encoding="utf-8"))
    assert d["description"].startswith("The IPF map") and d["screenshot"] == md.with_suffix(".png").name
    assert md.with_suffix(".png").read_bytes() == b"\x89PNG fake"


def test_component_sizes_of_large_not_indexed_regions():
    """Not-indexed pixels without a grain (grain id 0) get the size of their connected region, so a large region is
    never taken for a <= 4 px void (cleanup fill, both backends)."""
    from ti64app.backends.hexgrid import component_sizes, neighbour_table

    y, x = np.mgrid[0:6, 0:8].astype(float)
    nbr = neighbour_table(x.ravel(), y.ravel(), np.ones(48, bool), hex_grid=False)
    m = np.zeros((6, 8), bool)
    m[0:3, 0:4] = True            # 12 px region
    m[5, 7] = True                # 1 px
    s = component_sizes(m.ravel(), nbr).reshape(6, 8)
    assert s[1, 1] == 12 and s[5, 7] == 1 and s[4, 4] == 0


def test_error_collector_takes_logged_exceptions():
    import logging

    from ti64app import breadcrumbs as bc

    bc.clear()
    bc.install()
    try:
        1 / 0
    except ZeroDivisionError:
        logging.getLogger("ti64.test").error("Could not draw the map", exc_info=True)
    e = bc.errors()[-1]
    assert e["type"] == "ZeroDivisionError" and "Could not draw" in e["what"] and "Traceback" in e["traceback"]
