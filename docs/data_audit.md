# Data audit (2026-10-31)

Check of the test data in `data/test/` before any feature is built: what the files contain and
whether **pymtex** (`mtex` 1.0.0 on PyPI) and **MATLAB R2025b + MTEX 7.2.0** load them the same
way. No analysis features yet.

Scripts (in the repo, write nothing into `data/` except the crops):

| Script | Purpose |
| --- | --- |
| `scripts/audit_ang.py` | Text pass over the .ang (header, columns per row, phase/CI/IQ/Fit/SEM statistics); JSON to stdout |
| `scripts/audit_pymtex.py` | `loadEBSD(f, setting=2)`; summary JSON + per-pixel export (.npz) to a scratch folder |
| `scripts/audit_mtex.m` | `EBSD.load(f,'setting',2)` (run through the MATLAB MCP server); summary + per-pixel export (.h5) |
| `scripts/compare_loads.py` | Compares the two exports with each other and with the raw .ang, pixel by pixel |
| `scripts/make_crops.py` | 300 x 300 centre crops to `data/test/crops/` (+ provenance .json) |

## Environment

| Item | Version |
| --- | --- |
| Python (conda env `ti64`) | 3.12.16 |
| mtex (pymtex) | 1.0.0 |
| numpy / h5py / orix | 2.5.4 / 3.16.0 / 0.15.0 |
| kikuchipy / pyebsdindex | 0.13.1 / 0.3.10.1 |
| matlabengine | 25.2.2 |
| MATLAB | R2025b Update 5 (25.2.0.3177638), via the MATLAB MCP server |
| MTEX (MATLAB) | 7.2.0 at `C:\tools\mtex\mtex-7.2.0` |

## .ang maps

All three files: TSL/OIM ANG `VERSION 7`, `GRID: HexGrid`, XSTEP 0.2 um, YSTEP 0.173205 um,
`CartesianAlignment a || x, c* || z`, tilt 70 deg. Phases in the header: **Phase 1 = Ti (alpha)**
(Symmetry 62 → 6/mmm, a = 2.943, c = 4.698 A), **Phase 2 = Ti (beta)** (Symmetry 43 → m-3m,
a = 3.310 A). The beta block comes first in the header, but the phase numbers are 1 = alpha, 2 = beta.

| | JU02 | RI02 | RM02 |
| --- | --- | --- | --- |
| File | `EBM_Ti64_JU02_C_Area 3_OIM Map 1_Rescan_Rescan_cleaned.ang` | `EBM_Ti64_RI02_C_Area 1_OIM Map 1_Rescan_cleaned.ang` | `EBM_Ti64_RM02_C_Area 1_OIM Map 1_Rescan_cleaned.ang` |
| Size | 223 MB | 226 MB | 227 MB |
| NCOLS_ODD / EVEN / NROWS | 1501 / 1500 / 1733 | 1513 / 1512 / 1741 | 1514 / 1513 / 1745 |
| Points (file = header = pymtex = MTEX) | 2 600 367 | 2 633 263 | 2 641 058 |
| Map size X x Y (um) | 300.0 x 300.0 | 302.4 x 301.4 | 302.6 x 302.1 |
| Grid in both backends | 1733 x 1501 hex, 866 padding | 1741 x 1513 hex, 870 padding | 1745 x 1514 hex, 872 padding |
| Not indexed (phase −1, CI −1, Euler 4π) | 6 414 (0.25 %) | 4 128 (0.16 %) | 2 126 (0.08 %) |
| alpha | 2 592 744 (99.71 %) | 2 627 035 (99.76 %) | 2 637 392 (99.86 %) |
| beta | 1 209 (0.046 %) | 2 100 (0.080 %) | 1 540 (0.058 %) |
| **% indexed** | **99.75 %** | **99.84 %** | **99.92 %** |
| % with CI > 0.1 | 95.11 % | 97.20 % | 92.57 % |
| CI alpha p5 / median / mean / p95 | 0.102 / 0.173 / 0.176 / 0.261 | 0.111 / 0.186 / 0.190 / 0.280 | 0.095 / 0.154 / 0.154 / 0.216 |
| CI beta median (p5–p95) | 0.093 (0.059–0.130) | 0.112 (0.067–0.156) | 0.093 (0.067–0.120) |
| CI min of indexed points | 0.053 | 0.054 | 0.053 |
| IQ alpha p5 / median / p95 | 38 199 / 51 060 / 68 120 | 40 830 / 54 929 / 74 022 | 36 601 / 45 761 / 57 264 |
| IQ beta median | 38 614 | 42 847 | 38 060 |
| Fit | 180.000 in every row | 180.000 in every row | 180.000 in every row |
| SEM | 0 in every row | 0 in every row | 0 in every row |
| PRIAS columns | declared, **not present** | declared, **not present** | declared, **not present** |
| Load time pymtex / MTEX | ~1 s* / 3.9 s | ~1 s* / 4.0 s | ~1 s* / 4.0 s |

\* pymtex load time measured on the second load in the session (the first JU02 load took 4.7 s); RSS after load ~0.54 GB.

### Findings

1. **PRIAS columns are declared but absent.** `COLUMN_COUNT: 13` and `COLUMN_HEADERS` list
   PRIAS Bottom Strip / Center Square / Top Strip, but every data row of all three maps has
   10 columns (full-file check). MTEX 7.2 names the 10th column `fit_prias`; pymtex names it `fit`.
   PRIAS images (needed for a "pseudo-BSE"/forescatter view) are **not available from these exports**;
   they would have to be re-exported from OIM or computed from the .up2 patterns.
2. **Fit is not stored** (constant 180.000) and **SEM is not stored** (constant 0). Neither can
   serve as a quality metric; use CI and IQ only. The app should hide Fit/SEM when they are constant.
3. **Retained beta is almost absent as indexed pixels** (0.05–0.08 %, low CI ~0.09–0.11). The
   prior-beta reconstruction therefore relies on the alpha orientations only, and the parent
   symmetry must come from the header phase (present in all three files) — the fallback
   `crystalSymmetry('m-3m',[3.32 ...])` from EBSD_part2.m is not needed for these maps.
4. **These are cleaned maps.** No indexed point has CI < 0.053 and phase-(−1) points are rare,
   consistent with OIM cleanup (e.g. grain CI standardization/dilation). The input-state toggle
   must therefore default to *OIM-cleaned* for these files (the file names end in `_cleaned`).
   Which OIM cleanup steps were applied is not in the header → **open question**.
5. Not-indexed points have Euler angles 4π (12.56637) in the file and phase −1; both loaders
   mark them notIndexed.

### pymtex vs MTEX 7.2 load (setting 2)

Compared pixel by pixel (`scripts/compare_loads.py`) on all three full maps and all three crops:

| Quantity | Result |
| --- | --- |
| Pixel set (x, y) | identical; both = file |
| Phase per pixel | identical to the file (100 %) |
| CI, IQ | identical (max abs diff 0) |
| Orientations | max raw-quaternion angle 4.8e-6 deg (float rounding); symmetry-reduced misorientation 0 |
| Order | MTEX: grid order, `prop.oldId` = file row (1-based) for every pixel, NaN for padding. pymtex: grid order, `isPadding` mask; map to file rows by (x, y) |
| Padding | 1 pixel at the end of each even (short) hex row: MTEX phaseId = NaN, oldId = NaN; pymtex phase 0 + `isPadding` |
| Effect of `setting 2` | q_loaded = R · g_file with R = 180 deg about [1 −1 0] (Bunge (90, 180, 0)), a constant specimen-frame rotation, the same in both backends (= MTEX `EulerCorrection` (315, 180, 45), spread 0). No crystal-frame correction (alpha X‖a, Y‖b*, Z‖c in both). |
| Crystal frames | alpha `X||a, Y||b*, Z||c`, 6/mmm, a=b=2.943, c=4.698; beta m-3m, a=3.31 — same in both |
| Plotting convention | both default to east = +x, outOfScreen = −z (y down, Z into screen) after loading; matches the convention required in CLAUDE.md |
| Property names | pymtex: `ci, iq, fit, sem_signal`; MTEX: `ci, iq, fit_prias, sem, oldId` → **the backend layer must map names** |
| EBSD class | pymtex `EBSD` (gridded, `columnMajor` layout); MTEX `EBSDhex` (`dHex` 0.1155, `isRowAlignment` 1, `offset` 1) |

**Conclusion:** the two loads are identical; the differences are only the property names and
the padding/ordering representation. Visual check: `projects/_audit/JU02_crop300_alpha_IPFZ_pymtex.png`
and `..._mtex.png` (alpha, ipfHKLKey, IPF-Z) show the same map.

IPF key API differs between backends: MATLAB sets `key.inversePoleFigureDirection = zvector`; pymtex uses
`ipfHKLKey(cs, ipfDirection=vector3d.Z)` (or `key.ipfDirection`). Assigning `inversePoleFigureDirection`
in pymtex is **silently ignored** (a new Python attribute is created) and the key stays at its default
direction. The backend layer must translate this.

## Reference-frame check against the bachelor thesis (EBMX = JU02)

Note on sources: the **Research Assignment** is `matlab_reference/EBSD_part2.m` and its logs/outputs
(`data/reference/`, see docs/beta_reconstruction_method.md); it uses setting 2. The **bachelor thesis**
(below, "the thesis") is a separate work (texture and elasticity) whose EBSD figures were made with other code.

Reference: `C:\Claude\literature\F4-BP-2026-Vondracek-Matej-main.pdf`, Fig. 4.2 (orientation map
of EBMX, PDF page 44; axes S up, T3 right), Fig. 4.3 (alpha IPFs for S, T3, T4, turbo colour map, max ≈ 1.8).
Text (sec. 3, p. 39): S = build direction; T3, T4 ⟂ S; on the EBSD samples only S is reliably known.
The thesis figures were made with MTEX, not OIM, so this checks consistency with the earlier workflow,
not against OIM.

1. **Map identity and layout:** JU02 is the EBMX map. With x right, y down (both backends' default)
   the layout matches Fig. 4.2 exactly (no flip/rotation): `projects/_audit/JU02_alpha_keys_dirs_pymtex.png`.
   → In the map, **T3 = +x, S = −y (up), T4 = map normal (z)**.
2. **Map colours:** Fig. 4.2 is reproduced by the alpha IPF-**Z** map (TSL/HSV-type key; HKL key is
   much darker). IPF-X/Y maps do not match. IPF-Z is antipodally invariant, so it cannot tell
   setting 0/1/2 apart.
3. **In-plane frame (Fig. 4.3):** ODF from alpha pixels with CI ≥ 0.1, de la Vallée Poussin kernel halfwidth 10°,
   IPDF values at [0001] / [10-10] / [2-1-10] (MTEX 7.2):

   | Load | IPF along y | IPF along x | IPF along z (max) |
   | --- | --- | --- | --- |
   | `setting 0` (no correction), also 3 and 4 | 1.48 / 1.32 / 0.67 | 0.82 / 1.24 / 0.48 | 0.26 / 0.67 / 0.76 (1.85) |
   | `setting 2` (= setting 1, app default) | 0.83 / 1.24 / 0.47 | 1.49 / 1.33 / 0.67 | 0.26 / 0.67 / 0.76 (1.85) |
   | thesis Fig. 4.3, read from colours | S: ~1.5 / ~1.2 / ~0.7 | T3: ~1.0 / ~1.4 / ~0.4 | T4: ~0.3 / ~0.8 / ~0.9 (~1.8) |

   `projects/_audit/JU02_alpha_IPDF_setting0_mtex.png` looks the same as Fig. 4.3 (S = y, T3 = x, T4 = z).
   **The thesis texture figures therefore correspond to Euler angles that were not converted to the map frame
   (setting 0)**; with setting 2 (used by EBSD_part2.m — the Research Assignment — and by this app) the S and T3 IPFs swap. T4 (normal)
   is the same in both. Settings 1 and 2 give the same physical alignment (setting 1 rotates the
   coordinates instead of the Euler angles); settings 3/4 give the same IPFs as setting 0.
4. **Consequence:** in-plane frame-dependent results (IPF-X/Y, pole figures, Schmid factors for the 4PB
   stress along X, crack-plane traces) differ by an x↔y swap between the thesis convention and setting 2.
   Grain segmentation, KAM, prior-beta reconstruction and grain size do not depend on it. Only an OIM
   figure with an in-plane reference (IPF along RD/TD or a pole figure from OIM) can decide which one is
   physically correct; **the app keeps setting 2 (CLAUDE.md) until that is checked** → `docs/decisions.md`.

Side note from the thesis text (p. 39): OIM cleanup = grain dilation of single pixels with CI < 0.5,
then removal of pixels with CI < 0.1. The files in data/test still contain 2.6–7.4 % indexed points
with 0.05 ≤ CI < 0.1, so they were exported before the CI < 0.1 removal (or that step was done in MTEX).

MTEX 7.2 note: `ebsd.how2plot` is a dependent property without a setter (`ebsd.how2plot.east = xvector`
from EBSD_part2.m fails). It already has the required value after loading; the +ti64 port has to set it
through the MTEX 7 API (plotting-convention preference / `plottingConvention` object), not by assignment.

## Test crops (`data/test/crops/`, git-ignored)

300 hex rows x 300 columns from the **map centre**, starting on an OIM "odd" row so the hex shift is
kept; original header with NCOLS_ODD = NCOLS_EVEN = NROWS = 300 (so no padding); x, y shifted to start at 0.
Provenance in the `.json` next to each crop. 60 x 51.8 um, 90 000 points.

| Crop | Source rows / cols (0-based) | Offset x0, y0 (um) | % indexed | alpha / beta / not indexed |
| --- | --- | --- | --- | --- |
| JU02 | 716–1015 / 600–899 | 120.0, 124.015 | 99.85 % | 89 841 / 24 / 135 |
| RI02 | 720–1019 / 606–905 | 121.2, 124.708 | 99.51 % | 89 455 / 108 / 437 |
| RM02 | 722–1021 / 606–905 | 121.2, 125.054 | 99.69 % | 89 638 / 79 / 283 |

Checks: each crop loads in both backends as a 300 x 300 hex grid without padding; all values
(Euler, IQ, CI, phase, SEM, Fit) are identical to the source rows; pymtex vs MTEX misorientation 0.
Note: 300 x 300 px at 0.2 um is 60 um — smaller than a prior-beta grain (area-weighted ECD ~94 um on
JU02), so these crops are for unit tests of loading/cleanup/grains only.

**Reconstruction crops** (`*_crop1000.ang`, same rule, 1000 x 1000 points = 200 x 173 um, 1 000 000 points,
~89 MB each) contain whole prior-beta grains and are used for reconstruction tests. The JU02 reference
numbers are still checked on the full map only (slow integration tests).

| Crop 1000 | Source rows / cols (0-based) | Offset x0, y0 (um) | % indexed | alpha / beta / not indexed |
| --- | --- | --- | --- | --- |
| JU02 | 366–1365 / 250–1249 | 50.0, 63.393 | 99.76 % | 997 195 / 392 / 2 413 |
| RI02 | 370–1369 / 256–1255 | 51.2, 64.086 | 99.84 % | 997 636 / 759 / 1 605 |
| RM02 | 372–1371 / 256–1255 | 51.2, 64.432 | 99.87 % | 998 131 / 543 / 1 326 |

## .up2 pattern file

`Ti64_DED_Area 2_OIM Map 2.up2` (43 236 007 258 B). Header read only (struct decode + PyEBSDIndex
`get_pattern_file_obj(...).read_header()`, no patterns loaded):

| Field | Value |
| --- | --- |
| UP2 version | 4 |
| Pattern size | 60 x 60 px, uint16 |
| Header length (first pattern offset) | 58 B |
| Grid | 3001 columns x 2001 rows, **square** (hex flag 0) |
| Step | 0.5 x 0.5 um → map 1500 x 1000 um |
| Patterns | 6 005 001 |
| Extra patterns / non-contiguous | 0 / no |
| Size check | 58 + 6 005 001 · 60 · 60 · 2 B = 43 236 007 258 B = file size (exact) |

Note: this is a **DED** sample (file name), not EBM like the three .ang maps; there is no matching
.ang for it in data/test. 60 x 60 patterns (binned 1 pattern = 7.2 kB) → indexing in chunks with PyEBSDIndex.

## Literature (`C:\Claude\literature\`, outside the repo)

132 PDFs at the top level, plus `czech/` (1 thesis), `samples/` (theses/manuscripts incl. .docx) and
`OPST 47 Trojan/` (4 project .docx). Directly relevant to the app's methods:

| Topic | File | DOI |
| --- | --- | --- |
| Parent-grain reconstruction thresholds (EBM/AM Ti64) | `Yang-etal-IJLMM-2025-…pdf` (also as `Analysis and optimisation of parent grain reconstruction…pdf`) | 10.1016/j.ijlmm.2025.07.009 |
| Variant graph method | `The variant graph approach to improved parent grain reconstruction.pdf` | 10.1016/j.mtla.2022.101399 |
| MTEX parent grain reconstructor | (Niessen et al. 2022, J. Appl. Cryst.; not found as a separate PDF) | 10.1107/S1600576721011560 |
| OR refinement (iterative) | `Iterative Determination of the Orientation Relationship Between Austenite and Martensite.pdf` (= `s11661-016-3462-2.pdf`) | 10.1007/s11661-016-3462-2 |
| Grain detection algorithm | `Grain detection from 2d and 3d EBSD data—Specification of the MTEX algorithm.pdf` | 10.1016/j.ultramic.2011.08.002 |
| Prior-beta orientation | `A method to determine the orientation of previous beta phase in Ti64.pdf` | – |
| GND / KAM | `Resolving the geometrically necessary dislocation content by conventional EBSD.pdf` | – |
| 4-point bending | `4pb_article.pdf` | – |
| Own bachelor thesis (texture, elasticity; EBMX = JU02 figures). Not the Research Assignment, which is EBSD_part2.m + its logs | `F4-BP-2026-Vondracek-Matej-main.pdf` | – |

Duplicates (same size): `s11661-009-0064-2.pdf` = *Crystallography of Fatigue Crack Initiation…*,
`s11661-010-0397-x.pdf` = *The Origin of Microstructural Diversity…*, `srep26039-1.pdf` =
*Revealing martensitic transformation…*, `s41598-017-06504-5.pdf` = *The influence of porosity…*,
the two Yang 2025 copies (slightly different sizes), and `Kovarik2022b` (top level and `samples/`).
DOIs marked "–" are still to be filled from the PDFs.

## Other notes

- `docs/pymtex_recon_test.py` is already fixed for Windows (`resource` is optional; peak RSS is reported
  as NaN on Windows). The ported backend uses `psutil`.
- Peak RAM: pymtex load ~0.5 GB per map; the text audit holds one map as float32 (~100 MB). Well below 8 GB.

## Open questions (for the user)

1. Which OIM cleanup steps were applied to the `_cleaned` maps (needed for the input-state preset)?
2. Is there a non-cleaned (raw) .ang for JU02/RI02/RM02 to test the *raw* preset?
3. PRIAS: re-export the .ang with PRIAS columns from OIM, or compute virtual BSE from patterns later?
4. Build direction relative to the map X/Y for each sample (sample sheet) — needed for IPF reference direction.
   (Thesis: JU02 = EBMX with S = −y, T3 = +x, T4 = normal.)
5. ~~OIM screenshot of JU02~~ → compared with the thesis (section above). Remaining: an OIM figure with an
   in-plane reference (IPF-RD/TD map or OIM pole figure) to decide between setting 2 and the thesis convention.
   (Questions 1–4 deferred by the user, 2026-10-31.)

## Repo skeleton (approved and created 2026-10-31)

```
pyproject.toml, environment.yml     package ti64app (src in python/); exact env versions; pytest markers slow, matlab
settings.example.toml               template; machine paths go in settings.local.toml (git-ignored)
                                    or %APPDATA%\ti64app\settings.toml
python/ti64app/
  settings.py                       machine paths + Settings (backend, IPF key per phase, FigureStyle)
  sample.py                         sample sheet: build direction, input state, 4PB geometry/load, map position
  project.py, recipe.py, jobs.py    projects/<name>/ folder, recipe.json steps, job runner
  backends/base.py                  AnalysisBackend protocol, LoadParams, EBSDData, property-name map
  backends/pymtex_backend.py        + pymtex_plot.py (renderer, uses FigureStyle)
  backends/matlab_backend.py        lazy engine, ti64.setupMtex, "unavailable" instead of errors
  io/ang.py, io/up2.py, io/bse.py   .ang header/columns; .up2 header + memory-mapped chunks (read only); BSE stub
  modules/                          registration.py, crack.py, bending.py, indexing.py (stubs; consume results only)
  gui/app.py, mcp_server.py, cli.py
matlab/+ti64/                       setupMtex.m (MTEX on the path from the settings), readSettings.m
matlab/+ti64/+plot/                 applyStyle.m (shared FigureStyle)
matlab/tests/                       SetupTest.m (matlab.unittest)
scripts/                            audit_ang.py, audit_pymtex.py, audit_mtex.m, compare_loads.py, make_crops.py
tests/unit/                         300x300 crops, synthetic files
tests/integration/                  full maps, @pytest.mark.slow
tests/conformance/                  both backends (@pytest.mark.matlab); grows with every core operation
docs/                               data_audit.md, decisions.md, beta_reconstruction_method.md
```

Next: `load_ebsd` in both backends (from the audit scripts) with the load check above as the first
real conformance test (same pixel set, phases, CI/IQ, orientations ≤ 1e-4 deg).
