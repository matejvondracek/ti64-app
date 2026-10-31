"""Phase 3 chain on a full map, one backend per process: prior-beta reconstruction + all its figures,
time + peak RAM per step, PNGs for visual check.

Usage:  python scripts/run_phase3_full.py JU02 pymtex --angle 270 [--or-mode limit|burgers|refined] [--sweep]
Writes projects/phase3_<map>_<backend>[_<orMode>]/ (git-ignored). The build direction must be given (never
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("backend", choices=["pymtex", "matlab"])
    ap.add_argument("--angle", type=float, required=True, help="build direction angle (from +x toward +y)")
    ap.add_argument("--input-state", default="oim_cleaned")
    ap.add_argument("--or-mode", default="limit", choices=["limit", "burgers", "refined"])
    ap.add_argument("--sweep", action="store_true", help="also run the default parameter sweep (6 runs)")
    a = ap.parse_args()

    import psutil

    from ti64app import service
    from ti64app.backends import get_backend
    from ti64app.ops_recon import RECON_FIGURES
    from ti64app.project import Project

    f = glob.glob(str(REPO / "data" / "test" / f"*{a.map}*.ang"))[0]
    root = REPO / "projects" / (f"phase3_{a.map}_{a.backend}" + ("" if a.or_mode == "limit" else f"_{a.or_mode}"))
    shutil.rmtree(root, ignore_errors=True)
    p = Project.create(root.name, base=root.parent, backend=a.backend)
    proc = psutil.Process()
    be = get_backend(a.backend)
    log = []

    def step(op, params):
        t = time.perf_counter()
        s = service.run(p, op, params)
        mem = {"py_peak_GB": round(proc.memory_info().peak_wset / 2**30, 2)}
        if a.backend == "matlab":
            mem["matlab_GB"] = round(psutil.Process(be.pid()).memory_info().rss / 2**30, 2)
        row = {"op": op, "kind": params.get("kind", ""), "s": round(time.perf_counter() - t, 1), **mem}
        log.append(row)
        print(json.dumps(row), flush=True)
        return s

    service.run(p, "sample.set", {"map_id": a.map, "specimen_id": a.map, "map_file": f,
                                  "input_state": a.input_state, "build_direction": {"angle_deg": a.angle}})
    step("ebsd.load", {"map_id": a.map})
    r = step("recon.run", {"map_id": a.map, "params": {"orMode": a.or_mode}})
    s = json.loads((p.root / r.outputs["summary"].path).read_text())
    print(json.dumps({k: s[k] for k in ("or_dev_deg", "or_used", "n_grains_min_ecd", "n_grains_all", "rec_frac",
                                        "mean_fit_deg", "bad_fit_frac", "ecd_aw_um", "bridged_pct", "split_pct")}))
    print(json.dumps(s["grain_shape"]["aspect_along_across"]), json.dumps(s["variants"]["n_variants_hist"]))
    for k in RECON_FIGURES:
        step("recon.figure", {"map_id": a.map, "kind": k})
    if a.sweep:
        step("recon.sweep", {"map_id": a.map, "base": {"orMode": a.or_mode, "sweep": []}})
    (p.root / "timings.json").write_text(json.dumps(log, indent=1))
    if a.backend == "matlab":
        be.close()


if __name__ == "__main__":
    main()
