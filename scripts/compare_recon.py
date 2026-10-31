"""Prior-beta reconstruction of a full map on both backends in three OR modes (default 1 deg limit,
ideal Burgers, refined), with the EBSD_part2 run-log lines. Usage: python scripts/compare_recon.py JU02"""
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))
from ti64app.backends import get_backend  # noqa: E402
from ti64app.backends.base import LoadParams, ReconParams  # noqa: E402

m = sys.argv[1] if len(sys.argv) > 1 else "JU02"
f = glob.glob(str(REPO / "data" / "test" / f"*{m}*.ang"))[0]
out_dir = REPO / "projects" / "phase3_compare"
out_dir.mkdir(parents=True, exist_ok=True)
results = {}
for be_name in ("pymtex", "matlab"):
    be = get_backend(be_name)
    h = be.load_ebsd(LoadParams(file=f, input_state="oim_cleaned"))
    for mode in ("limit", "burgers", "refined"):
        rh, res = be.reconstruct(h, ReconParams(orMode=mode), str(out_dir / f"reconstruction_log_{be_name}.txt"))
        be.release(rh)
        results[f"{be_name}/{mode}"] = res
        print(be_name, mode, json.dumps({k: res[k] for k in ("or_dev_deg", "or_used", "n_grains_min_ecd",
              "n_grains_all", "rec_frac", "mean_fit_deg", "bad_fit_frac", "ecd_aw_um", "bridged_pct", "minutes")}),
              flush=True)
(out_dir / f"compare_{m}.json").write_text(json.dumps(results, indent=1))
get_backend("matlab").close()
