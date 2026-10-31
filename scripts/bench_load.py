"""Load time and peak RAM of ebsd loading for the full maps, per backend.

Usage:  python scripts/bench_load.py [--backends pymtex matlab] [--maps JU02 RI02 RM02] [--json out.json]
Each (map, backend) runs in a FRESH subprocess so the Windows peak working set (psutil peak_wset)
belongs to that load only. Reported:
  load_s      backend load (pymtex loadEBSD / MATLAB EBSD.load into the session store)
  transfer_s  get_ebsd: arrays to Python in file order (MATLAB: via temporary HDF5)
  py_peak_GB  peak working set of the Python process
  ml_base_GB / ml_peak_GB   MATLAB process working set after MTEX start / peak (matlab backend)
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))


def worker(backend: str, file: str) -> dict:
    import psutil

    from ti64app.backends import get_backend
    from ti64app.backends.base import LoadParams

    proc = psutil.Process()
    be = get_backend(backend)
    out = {"backend": backend, "file": Path(file).name}
    t = time.perf_counter()
    if backend == "matlab":
        be.engine()
        out["engine_start_s"] = round(time.perf_counter() - t, 1)
        mproc = psutil.Process(be.pid())
        out["ml_base_GB"] = round(mproc.memory_info().wset / 2**30, 2)
    else:
        import mtex  # noqa: F401  (import cost not part of the load)
    out["py_base_GB"] = round(proc.memory_info().wset / 2**30, 2)
    t0 = time.perf_counter()
    h = be.load_ebsd(LoadParams(file=Path(file), input_state="oim_cleaned"))
    t1 = time.perf_counter()
    d = be.get_ebsd(h)
    t2 = time.perf_counter()
    out.update(n_points=d.n, load_s=round(t1 - t0, 1), transfer_s=round(t2 - t1, 1),
               total_s=round(t2 - t0, 1), py_peak_GB=round(proc.memory_info().peak_wset / 2**30, 2))
    if backend == "matlab":
        out["ml_peak_GB"] = round(mproc.memory_info().peak_wset / 2**30, 2)
        be.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", nargs="+", default=["pymtex", "matlab"])
    ap.add_argument("--maps", nargs="+", default=["JU02", "RI02", "RM02"])
    ap.add_argument("--json")
    ap.add_argument("--worker", nargs=2, metavar=("BACKEND", "FILE"), help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker:
        print("RESULT " + json.dumps(worker(*a.worker)))
        return
    results = []
    for m in a.maps:
        f = glob.glob(str(REPO / "data" / "test" / f"*{m}*.ang"))[0]
        for b in a.backends:
            r = subprocess.run([sys.executable, __file__, "--worker", b, f], capture_output=True, text=True)
            line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
            res = json.loads(line[7:]) if line else {"backend": b, "file": Path(f).name,
                                                     "error": (r.stderr or r.stdout)[-800:]}
            res["map"] = m
            results.append(res)
            print(json.dumps(res), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
