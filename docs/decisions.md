# Decisions and assumptions

## D1 (2026-10-31) .ang reference frame: EDAX setting 2, in-plane frame still to be verified
- Both backends load .ang with `setting 2` (CLAUDE.md). This applies q = R·g_file, R = 180° about [1 −1 0]
  (MTEX `EulerCorrection` (315°, 180°, 45°)); pymtex 1.0.0 and MTEX 7.2.0 agree pixel by pixel (docs/data_audit.md).
- The thesis texture figures for EBMX (= JU02; F4-BP-2026-Vondracek, Fig. 4.3) match uncorrected Euler
  angles (setting 0) instead: S/T3 IPFs swap relative to setting 2; the normal direction (T4) is unaffected.
- Status: **assumption**. Keep setting 2 until an OIM figure with an in-plane reference (IPF along RD/TD, or a
  pole figure from OIM) confirms it. Results that depend on the in-plane frame (IPF-X/Y, pole figures, Schmid
  factors for the 4PB stress along X, crack-plane traces) must state the setting they used.
- JU02 map axes from the thesis: T3 = +x, S (build direction) = −y, T4 = map normal.

## D2 (2026-10-31) Units in the sample sheet
- Microstructure: um and deg; stress: MPa (CLAUDE.md). Specimen geometry for 4-point bending is entered in mm
  and the load in N, because that is how bend specimens and test machines report them. Field names
  carry the unit (`outer_span_mm`, `load_N`, ...) so nothing is converted silently.
- Status: **confirmed by the user** (2026-10-31).

## D3 (2026-10-31) Machine-specific paths
- MTEX folder, MATLAB root and the literature folder are machine settings, read from `TI64_SETTINGS`,
  `settings.local.toml` (git-ignored) or `%APPDATA%\ti64app\settings.toml`, in that order
  (template: `settings.example.toml`). MATLAB uses the same lookup in `ti64.readSettings`; `ti64.setupMtex`
  adds MTEX to the path from it. Analysis settings stay in each project's recipe.

## D4 (2026-10-31) Build direction: required, map frame, angle convention
- Every map needs a sample sheet with a build direction before it can be loaded; the app never guesses it.
- Given either as a 3-vector in MAP coordinates or as an in-plane angle measured from map +x toward map +y
  (map loaded with setting 2: x right, y down, so positive angles turn clockwise on screen). Confirmed by
  the user. Example: JU02 (thesis EBMX), S = −y → 270°.

## D5 (2026-10-31) JEOL BSE metadata: parser not yet verified
- No JSM-IT500HR image + .txt pair is available yet. `io/bse.py` follows the JEOL `$CM_…/$SM_…` text format,
  accepts common key aliases, takes the pixel size from an explicit pixel-size key or from
  SM_MICRON_MARKER / SM_MICRON_BAR (rescaled by CM_FULL_SIZE / image width), and records which keys
  were used. Tested on a synthetic file only (`verified_format = False`). Check against a real file
  before using pixel size or stage coordinates for registration.
- **Update 2026-11-06 (verified on 197 real pairs in data/, see D17):** the real key is `$CM_STAGE_POSITION`
  (the alias `CM_STAGE_POS` never matched, so stage values were not read before) with the order X Y Z [mm] R T
  [deg] (4th value 241.12 = rotation; the parser had read it as tilt). The detector is `$SM_DETECTOR`. The pixel size
  now comes from `$CM_FIELD_OF_VIEW` / image width (µm or mm); it agrees with SM_MICRON_MARKER / SM_MICRON_BAR, whose
  bar length is in file pixels. Files are UTF-8 with a BOM (µ = 2 bytes); Latin-1 is the fallback.
  `verified_format = True` when the field of view is present.

## D6 (2026-10-31) Recipe reproducibility
- Outputs carry a CONTENT hash (array names/dtypes/shapes/bytes, canonical JSON), not a file hash, so
  container details (zip timestamps) do not matter. Inputs carry a fingerprint (size + first/last 4 MB).
- `replay` re-runs all non-stale steps in a scratch project (projects/<name>/cache/replay_*): same backend →
  outputs must be identical; with a backend override, arrays are accepted within tolerance (floats
  rtol 1e-6 / atol 1e-5, orientations ≤ 1e-3° up to quaternion sign).
- Changing a sample sheet marks all steps of that map stale; switching the project backend marks all
  backend steps stale.

## D7 (2026-10-31) Cleanup: port of the EBSD_part2.m noise cleanup to MTEX 7 / pymtex
- Presets from the setup guide ("Method notes", raw / OIM-cleaned toggle): raw = CI < 0.1 removed first;
  OIM-cleaned = no CI filter (CI already modified by OIM). Both: EBSD_part2 noise cleanup at 3°, indexed
  grains <= 2 px removed, not-indexed regions <= 4 px and the removed pixels refilled. Wild-spike removal
  (1-px grains at 5°) and halfQuadraticFilter are optional steps, off in both presets. The loaded map is
  never changed; cleanup creates a new map.
- MTEX 7 / pymtex `calcGrains` fills small not-indexed regions itself unless `alpha = 0` (622 voids on the
  JU02 crop with alpha 0, 2 with the default 3.1). Noise/void detection therefore uses `alpha = 0`, which
  reproduces the MTEX 6.1 behaviour EBSD_part2.m was written for.
- Fill: own grain-dilation-like algorithm, identical in both backends (python/ti64app/backends/hexgrid.py,
  matlab/+ti64/dilationFill.m): a pixel takes the orientation of the neighbouring grain with most
  neighbours (ties: fixed neighbour order W, E, NW, NE, SW, SE), repeated in sweeps. MTEX's `fill` is a
  nearest-neighbour interpolation and pymtex's `fill` took 12 s on 90 k points and left pixels unfilled.
  Result: identical per pixel in both backends.
- `calcGrains` in both engines also returns a MODIFIED map (not-indexed pixels within `alpha` of a grain
  marked indexed, orientation NaN). The app keeps the measured map and only attaches the grain ids.
- Remaining engine difference: which grain a not-indexed pixel next to a void is assigned to (alpha fill)
  differs on a few pixels (RI02 crop: 139 of 198 not-indexed pixels; indexed pixels identical), so boundary
  segment counts / class lengths near voids differ by <= 0.25 %.
- halfQuadraticFilter: MTEX 7.2 calls it `l1TVFilter` (old name obsolete); same l1/TV filter as pymtex.
  In MATLAB it is slow (47 s on 90 k points vs 1.2 s in pymtex).

## D8 (2026-10-31) GND: dislocation systems and gradient stencil
- Systems (user decision): basal, prismatic, pyramidal <a> + 1st-order pyramidal <c+a>, each family
  symmetrised separately: 24 edge + 9 screw = 33 systems; line energy edge 1, screw 1 - nu (nu = 0.34).
  Pantleon 2008, doi:10.1016/j.scriptamat.2008.01.050.
- Stencil: default '1hop' (symmetric least squares over the 6 hex neighbours) instead of MTEX's
  'oneSided'. The two engines choose different lattice basis vectors for the one-sided difference, so
  'oneSided' gives the same statistics but different per-pixel values (median 26 % on the JU02 crop);
  '1hop' does not depend on the basis and agrees per pixel (rel. 2e-7). The parameter stays selectable.
- GND, KAM and GROD are computed inside grains (gradient / kernel do not cross grain boundaries).

## D9 (2026-10-31) pymtex 1.0.0 GND bug, worked around
- `fitDislocationSystems` takes the known curvature components from the FIRST pixel only; if that pixel's
  curvature is NaN (e.g. next to a grain boundary), every pixel gets rho = 0 (seen on the RI02 crop).
  The pymtex backend calls the fit only on pixels with a fully known in-plane curvature
  (`gnd_map` in backends/pymtex_analysis.py). Remove the workaround once pymtex fixes it.

## D10 (2026-10-31) Retained beta and the noise cleanup
- Indexed beta is rare (0.05-0.08 % of the maps) and much of it sits in 1-2 px clusters, which the
  EBSD_part2 noise rule (indexed grains <= 2 px removed) deletes: full JU02 map 1209 -> 815 beta px
  (-33 %), JU02 300x300 crop 24 -> 0. The fill can also give beta orientations to neighbouring removed
  pixels. The alpha/beta interface map is correspondingly sparse (full JU02: 287 um of interface).
  If retained beta should be kept, set noise_max_indexed_px = 0 or exclude beta from the noise rule
  (not implemented; to decide).

## D11 (2026-10-31) Figures
- Each backend draws its own figures: pymtex with its native hex-grid map plotting (matplotlib),
  MATLAB with the hexRaster image of EBSD_part2.m + exportgraphics. Overlays (boundaries) are passed as
  vector data (segments + colours). pymtex draws maps in screen coordinates (pos·east, pos·north), so
  overlays are projected the same way.
- pymtex's ipfHKLKey for beta shows no sliver along [001]-[101]; the corrected beta key of EBSD_part2.m is
  the MATLAB default for beta (`ti64.renderKey`). IPF colours of all four keys agree between the
  backends per pixel (<= 4e-4).
- Not in the test data: Fit (constant 180) and PRIAS (declared, absent) -> clear "not available" errors.
  CI of OIM-cleaned maps is labelled "CI after OIM cleanup" with a warning; KAM/GND of OIM-cleaned maps
  carry a warning that grain dilation lowers them locally.

## D12 (2026-10-31) Prior-beta reconstruction: MTEX 6.1 reference vs MTEX 7.2 vs pymtex (full JU02)
- Port: matlab/+ti64/reconstruct.m and backends/pymtex_recon.py follow EBSD_part2.m step by step
  (parameters = ReconParams in backends/base.py, names as in the script's `par`). Input by default = the
  LOADED map with EBSD_part2's own noise cleanup (removed, not filled; user decision), noise grains with
  alpha = 0 (D7). The OR rule is a visible setting: orMode 'limit' (refined OR kept if <= orMaxDev = 1°),
  'burgers' or 'refined'. Run-log line format of EBSD_part2.m kept (`write_log` / `logFile`).
- Results (default parameters, vgThr 5, single merge 3°, incl 5):
  | run | OR dev | grains >= 5 um (all) | recFrac | meanFit | fit > 3° | ECDaw |
  | MTEX 6.1 reference | 0.82 refined | 47 (701) | 100.0 % | 3.38 | 50.7 % | 93.9 |
  | MATLAB MTEX 7.2, limit | 0.97 refined | 46 (708) | 100.0 % | 3.36 | 50.5 % | 94.4 |
  | MATLAB, ideal Burgers | – | 52 (790) | 99.9 % | 3.568 | 55.2 % | 89.6 |
  | pymtex, ideal Burgers | – | 53 (796) | 99.9 % | 3.566 | 55.1 % | 89.5 |
  | pymtex, refined | 1.01 refined | 44 (708) | 100.0 % | 3.35 | 49.1 % | 99.9 |
  | pymtex, limit (default) | 1.01 -> Burgers | 53 (796) | 99.9 % | 3.57 | 55.1 % | 89.5 |
  MTEX 7.2 merge sweep: 0.5° 768 / 3° 46 / 6° 26 / 8° 22 / progressive 3° 126 grains (reference 762 / 47 / 28 /
  23 / 134).
- Differences: (1) MTEX 6.1 -> 7.2: OR refinement 0.82 -> 0.97°, grain counts within ±1-8 %; cleanup with
  alpha = 0. (2) pymtex vs MATLAB with the SAME OR: equal within ~1 % (grains 53 vs 52, fit 3.566 vs 3.568).
  (3) The refined ORs differ (1.01 vs 0.97°), so with the 1° rule pymtex falls back to Burgers while MATLAB
  keeps its refined OR -> default results differ (53 vs 46 grains). Conformance is therefore judged with the
  same OR mode in both (user decision).
- MTEX's packet IDs assume {111}_p||{110}_c (steel); Ti packets ({110}beta || (0001)alpha) must be computed
  separately (to do).

## D13 (2026-10-31) Reconstruction outputs, Ti variants/packets, grain shape vs BD, figures
- Parent map: the reconstruction returns the LOADED map (same grid, file order) with the parent EBSD put back
  on the kept pixels; props parent_grain, recon_fit (deg), recon_state (0 not indexed, 1 removed noise,
  2 reconstructed alpha, 3 untransformed alpha, 4 retained beta). Pixels not indexed in the input stay not
  indexed (MTEX 7 / pymtex calcGrains index voids inside grains, D7). Both backends give identical states
  and phases on the crops. Parent grains + boundaries + pre-merge boundaries: `ReconData` (base.py).
- Beta symmetry from the map's phase list (the .ang header), not from indexed beta pixels: on small crops
  all beta can be removed as noise. Fallback m-3m, a = 3.32 A as EBSD_part2.m (both backends).
- Ti variant / packet IDs are computed in Python (modules/recon_analysis.py) against the 12 IDEAL Burgers
  variants, (110)beta || (0001)alpha, [1-11]beta || [2-1-10]alpha: packet = which of 6 {110}beta is parallel to
  the alpha c-axis (Ti packet convention, e.g. Stanford & Bate 2004 doi:10.1016/j.actamat.2004.08.034), variant
  = which of the 2 <111>beta in that plane is parallel to an alpha <11-20>. NOT MTEX's packetId (steel
  {111}_p || {110}_c) and not MTEX's variantId (24 IDs with a refined OR). Counted against the parent GRAIN mean
  orientation: MTEX's per-pixel parent orientation is the symmetric equivalent that makes every child variant 1.
  The grain mean is first reduced to a canonical cubic equivalent (largest |scalar quaternion part|), so the IDs
  do not depend on which equivalent a backend returns: same variant ID in pymtex and MATLAB on >= 99.98 % of
  reconstructed pixels of the three crop1000s (before this, the IDs were permuted per grain).
  Hexagonal frame X || a (checked in both backends). Deviations reported: JU02 crop c-axis to {110} median 2.2°.
- Conformance on the three crop1000s, ideal Burgers in both (test_phase3.py): reconstruction state and phase
  identical on JU02 / RM02, 19 of 10^6 pixels differ on RI02; parent orientations within 2° on >= 99.76 % of
  reconstructed pixels; grains 306/304, 308/305, 230/229 (pymtex/MATLAB); mean fit equal within 0.002°;
  pre-merge boundary length 2-3 % shorter in pymtex (boundary geometry of the two engines).
- Variant-selection statistics: per map share + mean fit per variant ID (EBSD_part2 summary); per grain number
  of variants / packets above 1 % of the grain and the largest-variant share (IDs are relative to each grain).
- Grain shape vs build direction (first choice, to confirm): caliper = extent of the pixel centres of a
  prior-beta grain projected on the in-plane BD (along) and its normal (across) + one step; aspect = along /
  across; edge grains excluded, ECD >= histMinECD (ISO 13067 / ASTM E2627 rules). Not computed if BD is
  > 20° out of the map plane. JU02: aspect (area-weighted) 1.22 pymtex / 1.29 MATLAB (different OR, D12).
- Figures of a reconstruction come from the backend that computed it (refused otherwise). Histogram bins are
  computed in Python and drawn by the backend (`ChartSpec`); discrete variant/packet colours = MATLAB hsv(n)
  computed in Python, so both renderers use the same colours. Parent grain map coloured by grain MEAN
  orientation (EBSD_part2), boundaries > hagbAngle 10° between grain means.
- pymtex smooths the alpha grains with smoothBoundary, MATLAB with smooth (old behaviour, as EBSD_part2);
  the results agree within the tolerances (conformance test_phase3.py).

## D14 (2026-10-31) Grain size (phase 4): definitions, rules, engine differences
- Split (user decision): each backend computes the MTEX parts - grain segmentation (grains.calc), perimeter and
  longest / shortest caliper (`grain_geometry`), colony merge (`merge_colonies`); ONE Python module
  (modules/grain_size.py) computes second-moment ellipses, line intercepts, statistics and tables on each
  backend's partition. Conformance compares the end results (tests/conformance/test_phase4.py).
- Alpha grains / laths (user decision): the lath partition is a grains.calc step, threshold configurable,
  default 15 deg. Consequence: at 15 deg the two laths of one packet (10.53 deg about <0001>) are already one
  grain, so the colony merge finds nothing and colonies = alpha grains (a warning says so). JU02 crop300:
  15 deg -> 508 grains used, ECD 1.56 / 3.52 um (number / area-weighted); 5 deg -> 581 laths, 1.52 / 3.29 um,
  merged to 524 colonies, 1.55 / 3.46 um. Use 5 deg for lath thickness and colony size if packets matter.
- Colonies (user decision): neighbouring alpha grains merged (transitively) when their MEAN orientations are
  within 3 deg of the Burgers type-1 alpha/alpha misorientation (10.53 deg about <0001>) = laths sharing one
  {110}beta || (0001)alpha (Ti packet). No reconstruction needed. Identical partition in both engines.
- Lath thickness: (1) minor axis 2b of the ellipse with the grain's second area moments (pixel centres + each
  cell's own moment: square s^2/12, hexagon 5 s^2/72). For a w x L rectangle 2b = 2w/sqrt3 = 1.155 w, so the
  minor axis overestimates the lath width; (2) linear intercept perpendicular to each lath's long axis: laths
  (major/minor >= 2) grouped by long-axis angle in 5 deg bins, one family of test lines per bin, spacing = step,
  sampling step/4; per lath the MEDIAN chord (the mean is lowered by short chords across the lath tips:
  1.07 vs 1.2 um on a synthetic hex lath); chords cut by the map edge or by unassigned pixels are dropped.
  Not stereologically corrected (2D section values).
- Why not MTEX fitEllipse: MTEX 7.2 `principalComponents` weights the boundary vertices (Mulchrone &
  Choudhury 2004, doi:10.1016/S0191-8141(03)00093-2) and scales the ellipse to the grain area; pymtex 1.0.0
  returns the exact second-moment ellipse, unscaled. They differ by design, so the app uses its own pixel
  moments in both.
- Calipers (MTEX, each engine): perimeter and longest caliper equal to ~1e-6; the SHORTEST caliper differs by up
  to 12 % on 2-4.5 % of (mostly < 60 px) grains (different minimum-width algorithms). The longest-caliper angle
  differs on ties of equally long diagonals of small symmetric outlines. Ellipse and intercepts are equal except
  for grains that took a different not-indexed pixel next to a void (D7; RI02 crop: 2 of 1767).
- Prior beta (user decision): width = mean linear intercept across the in-plane BD, length = along BD, ASTM E112
  convention: L = total chord length / (N_complete + 0.5 N_cut), chords cut by the map edge or ending at
  unassigned pixels counted half; test lines 1 um apart. Chords are recorded only in prior-beta grains with
  ECD >= histMinECD (5 um) and >= min_px; smaller fragments end chords like any boundary. Plus the per-grain
  caliper along / across BD of D13 (interior grains). The reconstructed boundaries are ragged at lath scale, so
  many short chords cross protrusions between two large grains (JU02 crop1000: 82 % of chords < 5 um lie
  between two grains >= 5 um): the E112 mean (~22 um) is much smaller than the per-grain calipers (~80 um,
  area-weighted). Chord histograms and fits are therefore LINE-LENGTH-weighted. Length along BD is a lower
  bound when grains are longer than the map (warning).
- Rules (ISO 13067 / ASTM E2627): grains < min_px (default 5, configurable) and grains touching the map edge
  are excluded from per-grain statistics. ASSUMPTIONS to check against the standards (their texts are not in
  C:\Claude\literature): min_px default 5; QA warnings below 100 px per grain on average and below 100 grains
  (E2627 asks for a step small compared with the grain size). The 0.2 um step gives alpha grains of ~86-140 px
  on average; lath thickness (~0.6 um = 3 steps) is at the resolution limit.
- Statistics: number-weighted log-normal = MLE of ln d (KS test, p approximate because the parameters are fitted);
  area-weighted = weighted MLE of ln d with weights = grain area (weighted KS distance). Histograms: bins from
  0 to the 99.5th percentile, Freedman-Diaconis width, <= 40 bins.
- Retained beta: indexed beta pixels / indexed pixels of the LOADED map and of the analysed (cleaned) map, plus
  the reconstruction's retained-beta pixels. The noise cleanup removes much of it (D10): JU02 full map 0.047 %
  loaded -> 0.031 % cleaned. EBSD at 0.2 um does not resolve beta ribs: a lower bound.
- Validation against OIM: `grainsize.compare_oim` reads an OIM Analysis grain file ('# Column k: ...' header,
  columns found by keywords), applies the same rules and compares means, log-normal fits and a two-sample KS
  test. Tested on a synthetic file only; NOT verified against a real OIM export (none available yet).
- MATLAB Engine + XLSX: importing the `matlab` package puts MATLAB's DLLs first on the search path, after which
  Python's pyexpat (openpyxl) fails to load; backends/__init__.py imports xml.parsers.expat first.

## D15 (2026-11-01) Map viewer and export (phase 5)

- **What is drawn by whom.** The colour of every measurement comes from the active backend: IPF colours from its
  IPF key (`ipf_colors`, `ipf_colors_quat`), value maps through its colour map (`measurement_colors`: MATLAB
  `parula`/`turbo`/`gray` in MATLAB via `+ti64/valueColors.m`, matplotlib equivalents in pymtex; 256 levels,
  k = round(255 t), same rule as `+ti64/render.m`). Which measurement a pixel shows is pure geometry, done once in
  `ti64app/raster.py` for both backends (brick cell layout of `+ti64/hexRaster.m` / EBSD_part2.m: a hex cell is
  dx x dy, shifted rows by dx/2). This is used by (1) the viewer tiles and (2) pixel-exact exports (native,
  2x/4x/8x), so both backends give the same pixel geometry (conformance: phase map pixel-identical).
  **Figure exports** (width in mm at a DPI; PNG/TIFF/SVG/PDF) are drawn entirely by the backend renderer
  (matplotlib / MATLAB `print`), with overlays and annotations as vectors (CLAUDE.md: one renderer per figure).
  *Check:* if pixel-exact exports must also be drawn end-to-end by MATLAB, the layout (`index_image`) and line
  rasterisation (`rasterize_segments`) would have to be ported to +ti64 (~60 lines); not done.
- **Viewer overlays** (HAGB, LAGB, α/β interface, Burgers types, prior-β GB, crack) are the same vector segments
  as in figures and exports, rasterised per tile by the tile server (constant screen width, 1 px default).
  Annotations and ROIs are vectors drawn by Leaflet in the browser. Exported files never come from the browser.
- **pymtex raster shortcut** (CLAUDE.md allows it): pymtex's native hex map picture samples at a quarter of the
  step (42 M pixels for a 2.6 M map) and is never 1 px per measurement, so the viewer and pixel-exact exports use
  the shared brick layout. pymtex figure exports still use its own `ebsd.plot` (forced to its raster style with
  enough pixels for the output resolution of the shown region, because one polygon per cell makes 24 MB SVGs).
- **"1 px per measurement" on a hex grid.** A hex map cannot be shown 1:1 with square pixels: native exports have
  one pixel per measurement, pixel = dx x dy = 0.2 x 0.1732 um (NOT square; written to the PNG pHYs / TIFF
  X/YResolution tags and the sidecar), and the shifted rows are drawn dx/2 to the LEFT of their true position
  (half a pixel cannot be shifted). At 2x/4x/8x the shift is exact (k/2 pixels). For correct geometry with square
  pixels use the width-in-mm mode. Width: native = number of measurement columns; even scales include the
  half column of the shifted rows.
- **Texts in pixel-exact exports** are not drawn (no font renderer shared by both backends); lines, arrows, points
  and ROI outlines are. The sidecar lists the skipped texts; the width mode draws everything.
- **Exact physical size**: figure exports are exactly round(width_mm / 25.4 * dpi) pixels wide (pymtex: figure
  width snapped to whole pixels, no tight crop; MATLAB: `print` with a fixed paper size). Without title, colour bar
  and legend the map fills the figure edge to edge. ROI = axis limits (pymtex raster resolution raised to keep
  the output resolution).
- **Sidecar** (`<name>.json` next to the file, also a recipe output): map file + fingerprint, input state, BD,
  resolved layer and source steps, IPF keys, colour limits, overlays with boundary lengths, annotations, ROI,
  grid, step, pixel size, image size, notes, backend + versions, figure style, recipe step id. PNG/TIFF also carry
  a short JSON description. SVG/PDF from MATLAB contain a creation date: replay reports them `not_compared`
  and compares the sidecar.
- **Crop** (`ebsd.crop`): copies the .ang rows inside the ROI's bounding box to `projects/<p>/maps/<id>.ang`
  (text unchanged, ORIGINAL coordinates kept so annotations and overlays line up with the parent map; hex crops
  start on an OIM odd row; NCOLS_ODD = NCOLS_EVEN). Both backends load the same file (no backend-specific crop).
- **Undo / redo** (`history.py`): snapshots of the small state files (project.json, recipe.json, samples,
  annotations.json) around every recorded action, 50 deep. Outputs of undone steps stay on disk until a new
  action discards the redo stack, then they are deleted. Cropped .ang files are not deleted.
- **Viewer zoom** uses whole zoom levels (factor 2): tiles are then rendered pixel-exact; fractional zoom left
  hairline seams between tiles in Chromium. Zoomed out, a tile pixel averages up to 4 x 4 samples.
- Prior-β boundaries are drawn black at 2x the boundary line width (EBSD_part2 draws them black); HAGB black 1x.
- Playwright: `scripts/gui_screenshots.py` clicks through every page and panel (44 screenshots);
  `tests/integration/test_gui_playwright.py` (slow) runs it. NiceGUI markers are mirrored to `data-testid`.

## D16 (2026-11-01) MCP tools, `ti64 run`, compare backends (phase 6)

- **Tool surface.** One dedicated tool per service operation (map `TOOL_FOR_OP`, the test fails when an op has no
  tool) plus `run_operation(op, params)` for anything by name. Nested inputs reuse the service-layer pydantic models
  or are derived from them at import time (all fields optional for overrides; same fields minus `map_id` for
  options), so defaults and types are defined once. Fields the caller did not set are not sent on (`exclude_unset`),
  so the op's preset / defaults still apply and the recipe records only what was chosen.
- **Stdio safety.** mcp 2.3 diverts fd 1 while serving, but only best effort; when it falls back it serves the wire
  through `sys.stdout`. pymtex's `calcParent2Child` prints its OR scan with `print()` in cp1252 ("124.40°"), which
  broke one demo run (`UnicodeDecodeError` in the client). The server now owns the wire: private duplicates of fd 0/1
  carry the protocol; fd 1 and `sys.stdout` go to stderr, fd 0 / `sys.stdin` to NUL (a child process inheriting the
  wire's stdin pipe hangs on Windows while the reader thread blocks in `ReadFile`). Tested with print, raw fd writes
  and child-process output over real stdio.
- **Compare backends runs in a scratch copy** (`cache/compare_<step>/`: state files copied, step outputs
  hard-linked), not in the project itself: two backends' steps in one recipe would change the "newest step"
  defaults, and recon / grain-size figures refuse a backend that did not compute the result (D13). The second engine
  uses the step references the first one resolved. Backend objects of the scratch project are released and the
  folder is deleted unless `keep_scratch`. The project recipe gets one `compare.backends` step (recorded, replayable).
- **What is compared.** Every number (and short string) of the op's JSON outputs, flattened (lists of at most 12
  values), with abs / rel difference; no pass / fail here (tolerances live in the conformance tests); > 1 % flagged
  in GUI / MCP. Run time / RAM go to the step metrics (not compared on replay). Figures: each engine's own PNG (data
  ops get a quick-look figure: IPF / parent IPF / value map / ECD histogram, `figure_kind` to choose); the montage
  scales the two to equal height for display only and uses matplotlib's bundled DejaVu Sans (the system font lookup
  depended on the environment and made replays differ).
- **`ti64 run recipe.json`** = `service.run_recipe` (replay is the same function into a scratch project): step ids
  kept so references stay valid; ids already in the target are refused; hand-written steps need only op + params
  (backend = project backend); stale steps skipped unless `--include-stale`; recorded content hashes compared
  (tolerance checks when the recorded project folder is next to the recipe). Relative map paths in `sample.set`
  are resolved against the working directory and recorded absolute.
- **Registration** in Claude Code is machine-local (`claude mcp add ti64 --scope local ...`): the command contains
  this machine's env path and the repository is public, so no `.mcp.json` is committed.

## D17 (2026-11-06) SEM image stitching (phase 6.5)

- **Detector filter.** Class from `$SM_DETECTOR` (BED-* / BSE / COMPO → BSE; SED / SEI / LED / UED / USD → SE), the
  file-name token as fallback (`…_BED-C_2`, `…_SED_1`); the .txt wins when both exist. One mosaic per detector; never
  mixed. data/test/stitch: 8 BED-C + 8 SED, all from the .txt and consistent with the file names.
- **Data bar.** Height = file height − `$CM_IMAGE_SIZE` height (= `$SM_PNU_HEIGHT`); verified on all 197 images in
  data/ (1280 → 64, 2560 → 128, 5120 → 256 px; the image area is always 4:3). Without .txt: the 4:3 rule, confirmed by
  detecting the strip (black + white only, text in both rows); dark overview images (23×, black chamber above the
  strip) need the known-height check, a free search additionally requires textured image above the strip.
  Manual `strip_px` as the last resort. The analysis pixels never contain the strip; the sidecar stores its height.
- **Stage → image mapping, measured, not assumed.** `auto` tries every signed axis permutation and keeps the one whose
  predicted overlaps correlate best. Results (JSM-IT500HR, stage rotation 241.12°, scan rotation 0):
  +stage Y → +row (down): data/test/stitch, NCC 0.63 vs ≤ 0.11 for the other maps;
  +stage X → −col (left): JU05 50× overview 9781–9783 (stage moved 3 mm along X only), NCC 0.90 vs 0.27.
  This is `DEFAULT_STAGE_MAP`, used when the stage moved along one axis only (the other axis cannot be decided from
  the data) and for the thumbnail preview. Consequence: in the test column, higher stage Y is lower in the image, so
  9776 (Y 20.370) is at the top and 9777 / 9778 (Y 20.455 / 20.470) are at the BOTTOM (strict stage order, but not
  "9777/9778 at the top" as the phase brief expected). Both measurements are at R = 241.12°; whether the mapping
  depends on the stage rotation is not tested (tiles with different R or T are refused anyway).
- **Stage scale / angle.** Fitted stage → mosaic transform on the test column: 99 290 px/mm along Y (nominal 100 000 at
  10 nm/px, −0.7 %) and a cross term of 553 px/mm (the column drifts 58 px in x over 100 µm of stage Y, ~0.3°);
  JU05 50× along X: 1012.6 px/mm (+1.3 %). Stage values have 1 µm resolution, so they are only the starting guess
  (search ± `search_um` = 3 µm); the registration places the tiles. `$MP_BEAM_SHIFT` is reported; it was constant
  within every set checked, a warning is written if it differs (the stage prior ignores it).
- **Registration.** Translation only. Coarse level (tiles downsampled to ~640 px): phase correlation of the
  predicted overlap (Hann window, 6 strongest peaks × 4 wrap-around interpretations) plus the best offset of a masked
  NCC surface (Padfield 2012, doi:10.1109/TIP.2011.2181402) inside the search window; every candidate is scored by
  NCC of the actual overlap. The NCC surface was added because phase correlation alone missed 4 of 7 pairs on the
  test column (narrow overlaps of 220–560 px, true offsets up to 50 px from the stage). Full resolution:
  `skimage.registration.phase_cross_correlation` (upsample 20) on the overlap at the coarse match, accepted when it
  does not lower the NCC. Pairs with NCC < `min_score` (0.3) or > √2·search from the stage prediction are rejected.
  Optional rotation check (± 1°, report only).
- **Global placement.** Weighted least squares (weight = NCC) over all good pairs, anchored to the stage positions with
  weight 1e-4, dropping the worst pair while its residual > 3 px (Fiji Grid/Collection stitching, Preibisch 2009
  doi:10.1093/bioinformatics/btp184; NIST MIST, Chalfoun 2017 doi:10.1038/s41598-017-04567-y). Tiles without a good
  pair are placed by the stage and flagged (`stage_only_tiles`, red border in the GUI). Without stage data all pairs
  are tried and the good matches must connect all tiles. On a single column the pair graph is a chain, so residuals are
  ≈ 0 by construction; they become informative on 2D grids (loops).
- **Placement and blending.** Tiles are placed at integer pixel positions (no resampling, raw pixels stay raw);
  subpixel positions are in the sidecar. Per-tile gain / offset from the overlap statistics (global LS, mean log gain
  0), then linear feathering (weight = distance to the tile edge). `blend = "none"`: each pixel from the tile whose edge
  is farthest, no normalisation (for measurements on raw pixels).
- **SE with the BSE layout.** Each SE tile is matched to the BSE tile with the same `$SM_IMAGE_NUMBER` (± 40 px) by
  NCC of Gaussian-smoothed intensities (σ 2 px; gradient NCC scored only 0.05–0.2), then subpixel phase correlation;
  same canvas and origin as the BSE mosaic, so the two align pixel for pixel. Test column: shifts ≈ (+1, +1) px for
  all 8 tiles, NCC 0.17–0.65, SE taken 12–43 s before BSE. SE stitched on its own lands within 0.6 px of the BSE
  layout (relative).
- **Info strip.** `none` (default); `keep`: the bottom tile's original strip under the mosaic (its image number and
  date), only for single-column mosaics. "Single column" = mosaic width − tile width ≤ 5 % of the tile width: the test
  column drifts 58 px in x (≈ 5 px per tile), more than the "few px" assumed in the brief, so a tighter tolerance would
  refuse the reference data set. `generate`: a new JEOL-style strip for any layout, laid out from the measured
  original (left fields from the left edge, camera at the centre, scale bar from the right edge; positions scale with
  the strip height). Font: DejaVu Sans bundled with matplotlib (identical everywhere → replays reproduce the pixels),
  condensed to 86 % width to match the JEOL glyph widths. Field starts match the original within 3 px (WD 389 vs 389,
  Std.-PC 743 vs 743); the scale bar is at the same pixels (1790–2290, rows 18–45). Scale bar = largest 1/2/5 × 10ⁿ µm
  not longer than the tiles' own bar (`SM_MICRON_BAR`). Fields that differ between tiles are shown as ranges.
- **Output.** `mosaic_<DET>.tif`: tiled, zlib, BigTIFF above 2 GB, pyramid (sub-IFDs, /2 per level down to ~1024 px)
  above 8192 px, resolution tags from the pixel size; written from a disk-backed array rendered in bands (RAM: tiles
  in an LRU of 12, downsampled tiles, cached FFT spectra; test column peak 0.9 GB). New recipe output kind `mosaic`,
  hashed over the file bytes (tifffile writes no timestamp; a replay reproduced the TIFF bit for bit), so verifying a
  large mosaic never decodes it. PNG preview (≤ 2048 px), JSON sidecar, CSV tile table.
- **Not covered by data here:** a 2D grid of real tiles (none in data/: every multi-tile set moved along one axis, and
  `JU04/stich along crack path` is not usable: 9466 / 9467 and 9469 / 9470 / 9472 share stage X **and** Y but show
  different fields, so the stage Y reading did not follow the moves). 2D grids are tested on synthetic data only.
