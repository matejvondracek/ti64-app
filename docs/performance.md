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

## Phase 2 chain on the full JU02 map (2 600 367 points)

`python scripts/run_phase2_full.py JU02 <backend> --angle 270` (one process per backend; peak = Windows
peak working set of the process so far). OIM-cleaned preset; grains at 15° / 2°; GND '1hop'.

| Step | pymtex (s) | pymtex peak RAM (GB) | MATLAB (s) | MATLAB peak RAM (GB, MATLAB process) |
| --- | --- | --- | --- | --- |
| load | 2.3 | 1.03 | 9.1 | 2.07 |
| cleanup | 4.4 | 1.64 | 17.9 | 3.78 |
| grains + boundary table | 4.7 | 2.13 | 23.3 | 3.85 |
| KAM / GROD / GOS | 0.4 / 0.5 / 0.5 | 2.13 | 3.6 / 1.3 / 1.3 | 3.85 |
| GND (alpha, 33 systems) | 21.8 | 3.03 | 146.9 | 3.85 |
| figures (IPF, CI, IQ, KAM, GND, boundaries, Burgers, key) | 1.3–11 each | 3.03 | 1.7–12 each | 3.85 |

- The GND fit is done in chunks (pymtex 250 k px, MATLAB 50 k px): without chunking pymtex peaked at
  7.8 GB and MATLAB ran out of memory (8.7 GB with 250 k chunks).
- Python process with the MATLAB backend: ≤ 0.7 GB (arrays arrive through temporary HDF5 files).
- Results of the two backends on the full map: cleanup identical; 18 830 vs 18 820 grains, 588 edge grains
  both; boundary lengths per class within 0.3 % (α/β interface 287 vs 295 µm); KAM, GND per pixel
  identical (median rel. diff ≤ 2e-7). Differences come only from not-indexed pixels next to voids
  (decisions D7).
