"""Phase 2 chain on a full map, one backend per process: time + peak RAM per step, PNGs for visual check.

Usage:  python scripts/run_phase2_full.py JU02 pymtex [--angle 270]
Writes projects/phase2_<map>_<backend>/ (git-ignored). The build direction must be given (never
guessed): JU02 = thesis EBMX, S = -y -> 270 deg.
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

FIGS = [{"kind": "ipf", "overlays": ["hagb"]}, {"kind": "ipf", "direction": "build"}, {"kind": "phase"},
        {"kind": "ci"}, {"kind": "iq"}, {"kind": "kam", "overlays": ["hagb"]}, {"kind": "grod"}, {"kind": "gos"},
        {"kind": "gnd"}, {"kind": "boundaries"}, {"kind": "burgers"}, {"kind": "ipf_key", "phases": ["Ti (beta)"]}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("backend", choices=["pymtex", "matlab"])
    ap.add_argument("--angle", type=float, required=True, help="build direction angle (from +x toward +y)")
    ap.add_argument("--input-state", default="oim_cleaned")
    a = ap.parse_args()

    import psutil

    from ti64app import service
    from ti64app.backends import get_backend
    from ti64app.project import Project

    f = glob.glob(str(REPO / "data" / "test" / f"*{a.map}*.ang"))[0]
    root = REPO / "projects" / f"phase2_{a.map}_{a.backend}"
    shutil.rmtree(root, ignore_errors=True)
    p = Project.create(root.name, base=root.parent, backend=a.backend)
    proc = psutil.Process()
    be = get_backend(a.backend)
    mproc = None
    log = []

    def step(op, params):
        nonlocal mproc
        t = time.perf_counter()
        s = service.run(p, op, {"map_id": a.map, **params})
        dt = time.perf_counter() - t
        if a.backend == "matlab" and mproc is None:
            mproc = psutil.Process(be.pid())
        row = {"op": op, "params": {k: v for k, v in params.items() if k != "map_id"}, "s": round(dt, 1),
               "py_peak_GB": round(proc.memory_info().peak_wset / 2**30, 2)}
        if mproc is not None:
            row["ml_peak_GB"] = round(mproc.memory_info().peak_wset / 2**30, 2)
        log.append(row)
        print(json.dumps(row), flush=True)
        return s

    step("sample.set", {"specimen_id": a.map, "map_file": f, "input_state": a.input_state,
                        "build_direction": {"angle_deg": a.angle}})
    step("ebsd.load", {})
    c = step("ebsd.cleanup", {})
    g = step("grains.calc", {})
    for k in ("kam", "grod", "gos", "gnd"):
        step("map.compute", {"kind": k})
    for fp in FIGS:
        step("figure", fp)
    summary = {
        "cleanup": json.loads((p.root / c.outputs["report"].path).read_text()),
        "grains": json.loads((p.root / g.outputs["summary"].path).read_text()),
        "steps": log}
    (root / "phase2_summary.json").write_text(json.dumps(summary, indent=1))
    print("SUMMARY", json.dumps({k: summary[k] for k in ("cleanup", "grains")}))


if __name__ == "__main__":
    main()
