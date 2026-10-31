# Performance: loading the full maps

Measured 2026-10-31 with `scripts/bench_load.py` (each map × backend in a fresh process; Windows
peak working set via psutil). Machine: Windows 11, 16 GB RAM, ti64 env (Python 3.12.16, mtex 1.0.0,
numba 0.68.1), MATLAB R2025b Update 5 + MTEX 7.2.0. Load = `load_ebsd` (object kept in the backend);
transfer = `get_ebsd` (per-pixel arrays to Python in file order; MATLAB via temporary HDF5).

| Map | Points | Backend | Load (s) | Transfer (s) | Total (s) | Peak RAM Python (GB) | Peak RAM MATLAB (GB) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| JU02 | 2 600 367 | pymtex | 1.2 | 0.5 | 1.7 | 1.03 | – |
| JU02 | 2 600 367 | matlab | 6.5 | 0.7 | 7.1 | 0.38 | 1.91 (0.72 after MTEX start) |
| RI02 | 2 633 263 | pymtex | 1.2 | 0.5 | 1.7 | 1.04 | – |
| RI02 | 2 633 263 | matlab | 6.1 | 0.7 | 6.8 | 0.38 | 1.96 (0.72) |
| RM02 | 2 641 058 | pymtex | 1.2 | 0.5 | 1.6 | 1.04 | – |
| RM02 | 2 641 058 | matlab | 6.1 | 0.7 | 6.8 | 0.38 | 1.98 (0.72) |

- MATLAB engine start incl. MTEX: ~3.7 s per process (once; the engine is persistent).
- pymtex: the very first load in a new environment takes ~4–5 s longer (numba compiles its kernels
  once and caches them in site-packages; no cache is written next to the data).
- Peak RAM per loaded map: ≤ 1.1 GB (pymtex process) and ≤ 2.0 GB (MATLAB process) — far below the
  8 GB limit. Both backends together with one map loaded each: ~3 GB.
- Re-run: `python scripts/bench_load.py --json out.json`.
