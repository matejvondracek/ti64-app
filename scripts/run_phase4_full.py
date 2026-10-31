"""Phase 4 chain on a full map, one backend per process: reconstruction (ideal Burgers by default, so both
backends use the same OR; D12), cleanup, grains, grain size + all figures, time + peak RAM per step, PNGs and
the CSV / XLSX tables for checking.

Usage:  python scripts/run_phase4_full.py JU02 pymtex --angle 270 [--hagb 15] [--or-mode burgers]
Writes projects/phase4_<map>_<backend>[_hagb<deg>]/ (git-ignored). The build direction must be given (never
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
    ap.add_argument("--hagb", type=float, default=15.0, help="lath boundary threshold (grains.calc hagb_deg)")
    ap.add_argument("--or-mode", default="burgers", choices=["limit", "burgers", "refined"])
    a = ap.parse_args()

    import psutil

    from ti64app import service
    from ti64app.backends import get_backend
    from ti64app.ops_grainsize import GS_FIGURES
    from ti64app.project import Project

    f = glob.glob(str(REPO / "data" / "test" / f"*{a.map}*.ang"))[0]
    root = REPO / "projects" / (f"phase4_{a.map}_{a.backend}" + ("" if a.hagb == 15 else f"_hagb{a.hagb:g}"))
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
    step("recon.run", {"map_id": a.map, "params": {"orMode": a.or_mode}})
    step("ebsd.cleanup", {"map_id": a.map})
    step("grains.calc", {"map_id": a.map, "grains": {"hagb_deg": a.hagb}})
    g = step("grainsize.run", {"map_id": a.map})
    s = json.loads((p.root / g.outputs["summary"].path).read_text(encoding="utf-8"))
    al, co, pb = s["alpha"], s["colony"], s["prior_beta"]
    print(json.dumps({
        "alpha_used": al["n_used"], "alpha_ecd_mean": al["ecd"]["mean"], "alpha_ecd_aw": al["ecd"]["area_weighted"]["mean"],
        "laths": al["n_laths"], "thickness_intercept_mean_median": [al["thickness_intercept"]["mean"],
                                                                     al["thickness_intercept"]["median"]],
        "minor_axis_mean_median": [al["thickness_minor_axis"]["mean"], al["thickness_minor_axis"]["median"]],
        "colonies_used": co["n_used"], "colony_ecd_mean": co["ecd"]["mean"], "merged": co["n_alpha_grains_merged"],
        "beta_n": pb["ecd"]["n"], "beta_ecd_aw": pb["ecd"]["area_weighted"]["mean"],
        "width_e112": pb["across_bd"]["mean_e112_um"], "length_e112": pb["along_bd"]["mean_e112_um"],
        "caliper_across_aw": pb["caliper"]["across_bd_um"]["area_weighted_mean"],
        "caliper_along_aw": pb["caliper"]["along_bd_um"]["area_weighted_mean"],
        "retained_beta": {k: s["retained_beta"][k]["fraction"] for k in ("loaded", "source_map")},
        "warnings": s["warnings"]}, indent=1))
    for k in GS_FIGURES:
        step("grainsize.figure", {"map_id": a.map, "kind": k})
    (p.root / "timings.json").write_text(json.dumps(log, indent=1))
    if a.backend == "matlab":
        be.close()


if __name__ == "__main__":
    main()
