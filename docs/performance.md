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

## Phase 4 grain size on the full JU02 map

`python scripts/run_phase4_full.py JU02 <backend> --angle 270 [--hagb 5]` (reconstruction with ideal Burgers,
OIM-cleaned preset, laths at 15° unless --hagb).

| Step | pymtex (s) | pymtex peak RAM (GB) | MATLAB (s) | MATLAB process RAM (GB) |
| --- | --- | --- | --- | --- |
| recon.run | 20.5 | 2.77 | 64.1 | 1.37 |
| grains.calc | 4.1 | 3.54 | 18.8 | 1.80 |
| grainsize.run (geometry, colonies, ellipses, ~36 lath-intercept passes, prior-beta intercepts, tables) | 31.2 | 3.54 | 37.6 | 1.81 (Python 1.22) |
| grainsize.figure (histograms / colony map) | 0.2-0.6 / 7.9 | 3.73 | 1-3 | 2.50 |

- Results: alpha grains used 16 326 / 16 322, ECD 1.758 / 1.758 um (area-weighted 9.228 / 9.230), lath thickness
  intercept mean 0.640 / 0.640 um (median 0.55), minor axis mean 1.202 / 1.203 um; prior beta (>= 5 um) 27 / 26
  grains, E112 width 21.90 / 21.91 um, length 20.83 / 20.84 um, calipers across / along BD 81.2 / 81.5 and
  95.8 / 96.1 um (area-weighted); retained beta 0.047 % loaded, 0.031 % after cleanup (pymtex / MATLAB).
- Laths at 5° (pymtex): 19 170 alpha grains used (ECD 1.70 um), 2 483 merged into 16 935 colonies (1.77 um).

## Phase 5 map viewer and export on the full JU02 map (2.6 M points)

`python scripts/make_demo_project.py --name phase5_full --map full --no-recon`, then
`python scripts/bench_viewer.py` (pymtex). MATLAB rows: same project, `backend="matlab"`.

| Step | s | peak RAM (GB) |
| --- | --- | --- |
| layer IPF: backend colours + cell layout (first time) | 2.6 | 1.28 |
| layer IPF again (disk cache, cache/viewer/) | 0.01 | 1.28 |
| layer KAM | 0.28 | 1.28 |
| base tile 256 x 256, overview (16 cells / px, 4 x 4 averaging) | 0.06 | |
| base tile 1:1 / 8x zoomed in | 0.003 / 0.002 | |
| HAGB overlay: segment index / tile at overview / tile at 1:1 | 0.15 / 0.21 / 0.004 | |
| export native PNG + HAGB (1501 x 1733 px) | 1.6 | 1.29 |
| export 2x TIFF + HAGB | 1.8 | 1.29 |
| export figure 120 mm @ 600 DPI PNG + HAGB (2835 px wide) | 9.9 | 2.02 |
| export figure 120 mm PDF + HAGB (vector boundary lines) | 32.2 | 2.52 |
| MATLAB: layer IQ (valueColors) / layer IPF (incl. loading the map in MATLAB) | 4.1 / 8.6 | 0.67 (Python) |
| MATLAB: export native PNG / figure 120 mm @ 600 DPI PNG | 2.2 / 9.7 | 0.67 (Python) |

Tiles are cached (LRU, 3000 tiles); layer colours are cached on disk per layer, recipe state and figure style.
