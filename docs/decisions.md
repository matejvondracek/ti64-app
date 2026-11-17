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

## D18 (2026-11-06) Samples and linked files: SampleInfo + MapInfo, format 2 migration (phase 6.6)
- **Model.** `sample.py`: `SampleInfo` = one specimen, entered once (ID e.g. JU04, material, process, heat treatment,
  4PB geometry + load, build direction in the specimen frame as an axis ±X/±Y/±Z, tension side ±Y, notes);
  `MapInfo` = one linked file (absolute path, kind ang / image / up2 / other, sample or Unassigned, size + mtime,
  input state, build direction in map coordinates, position, role tension / middle / compression, notes, and optional
  per-file overrides of the inherited values material / process / heat treatment / 4PB). A file belongs to one sample.
  The EBSD ops still read one `SampleSheet` per map: `Project.sample(map_id)` builds it from MapInfo + SampleInfo
  (`effective_sheet`), so no op changed. A map is usable once it has a sample, an input state and a build direction;
  otherwise the op stops with a plain message naming what to enter (never guessed).
- **Storage (project format 2).** `samples.json` (list of SampleInfo) and `files.json` (list of MapInfo) replace
  `samples/<map_id>.json`. `sample.set` (MCP `set_sample_sheet`, old recipes) still takes a full sheet: it creates the
  sample if needed and stores values that differ from the sample as per-file overrides, so the effective sheet equals
  the sheet given (replay of format 1 recipes gives the same results).
- **Migration.** On open, a format 1 project (samples/ folder, no files.json) is converted: one SampleInfo per
  specimen_id (from the first sheet of that specimen), one MapInfo per sheet with overrides where sheets of the same
  specimen differed. The old folder is kept as `.history/format1_samples/`; `project.json` gets `format_version: 2`.
  The undo snapshots are converted the same way, so undo across the migration restores format 2 files. The undo
  stack moved from `cache/history/` to `.history/undo/` (so "Clear cache" never deletes it).
- **Stale results.** Editing a file's sheet (or its sample, or moving it to another sample) marks that map's steps stale
  only if the effective values the results depend on change (input state, build direction, 4PB, position, file);
  notes, material, process and the sample ID do not.
- **Sample suggestions.** Auto-assign proposes the sample ID from the first `[A-Z]{2}\d{2}` token of the file name,
  else of the folder names (JU04 in `9445_MVH1_JU04_SED_1.bmp`, JU02 in `EBM_Ti64_JU02_C_Area 3_…ang`); the user
  confirms the list. The role is suggested from the position through the thickness: |Y| ≤ 10 % of half the
  thickness (0.05 mm without 4PB data) = middle; otherwise tension / compression from the sample's tension side, which
  the user enters (no sign convention is assumed). JEOL stage coordinates are shown next to the position fields but
  not converted to the specimen frame (the stage → specimen mapping is not known).
- **Recipe.** Every step records `sample_id` (explicit `sample_id` param, else the sample of its map, else the
  specimen of a `sample.set` sheet). Output folders are `outputs/<sample>_<step>/`; export names (`export.map`,
  `sem.stitch`, `sem.export`) start with the sample ID unless the name already does (JU02_C_Area3 stays as it is).
  Older projects keep their `outputs/<step>/` folders (`Project.find_step_dir` finds both).

## D19 (2026-11-06) Desktop GUI, autosave and recovery (phase 6.6)
- **Window.** `ti64-gui` (gui-script → `Scripts\ti64-gui.exe`, pythonw: no console, no conda activate) runs the
  NiceGUI server in a background thread (`ui.run_with` + uvicorn on a free local port) and shows it in a pywebview
  window (Edge WebView2). Chosen over NiceGUI's `native=True` because pywebview then runs in the main thread of one
  process: native file dialogs, bring-to-front and a pythonw launcher work without NiceGUI's window subprocess.
  `python -m ti64app.gui --browser` / `ti64 gui --browser` serve the same pages in a browser (Playwright, Claude,
  development); the GUI tests always use the browser mode.
- **One window per project.** The window writes `<project>/.window.lock` (pid, port). Opening a project whose lock
  belongs to a live process calls that window's `/api/ti64/focus` (restore + show + briefly on top) instead of opening
  it twice; opening another project from a window that already shows one starts a new window process.
- **No console.** Logs go to `%APPDATA%\ti64app\logs\ti64-gui.log` (rotating, 3 × 2 MB). Start-up errors (missing
  pywebview / WebView2, server not starting, unhandled exceptions) open a Windows message box that offers to open the
  log; errors inside the app are plain-language notifications with an "Open log" button. Missing WebView2 runtime:
  the message names the Evergreen Bootstrapper (developer.microsoft.com/microsoft-edge/webview2) and the
  `ti64 gui --browser` fallback.
- **Windows dialogs.** Native pywebview dialogs in the desktop window, tkinter dialogs in browser mode on this machine,
  and a queue of answers in tests (`dialogs.MOCK`, or the JSON file named by `TI64_MOCK_DIALOGS` for Playwright).
- **Autosave.** Every state file is written atomically (temp file + rename): project.json, recipe.json, samples.json,
  files.json, annotations.json, gui_state.json, preferences. Sheet fields save on blur / Enter; Settings save on
  every change; there is no Save button and no prompt on close. The top bar shows "Saved" / "Saving..." / the error.
- **Working state.** `gui_state.json`: open tab, selected file / tree item, per map the layer, overlays and viewer
  bounds (restored with `initial_view`), unapplied values of every edit-menu section ("● not applied", restored as
  drafts), the export choices, the stitch options, the last folder.
- **Recovery.** recipe.json keeps its last 20 versions in `.history/recipe/` (also on undo); History can restore one
  (undoable). `running.json` is written while a recorded step runs; if the app closes during it, the next open finds
  the file of a dead process and offers "Rerun step" (the step was never added to the recipe, so nothing is half
  recorded).
- **Linked files.** size + mtime are stored when a file is linked and updated when a step uses it; the Files tab shows
  missing files greyed out with "Relocate" and changed files with a warning.
- **Save copy as.** Copies the state files, `outputs/`, `maps/`, `figures/` (hard links where possible) and the GUI
  state; not `cache/` or the undo history.
- **Compare to original.** The viewer gets a second tile layer: the same map kind from the `ebsd.load` step (IPF if
  the kind has no meaning before cleanup). It is swapped in client-side while the button is held (pointer or
  Space / Enter), same view and zoom; overlays and annotations are hidden meanwhile.
- **Mosaic export.** New op `sem.export` (PNG / TIFF / SVG with an embedded PNG / PDF, native or width at DPI,
  optional scale bar from the mosaic pixel size and the info strip); export.map unchanged. Both copy the result to the
  file chosen in the save dialog and write a JSON sidecar next to it.

## D20 (2026-11-06) True hexagons for hexagonal-grid maps (viewer + exports, both backends)
- **Processing unchanged** (MTEX EBSDhex in both backends). Only the drawing changes.
- **Brick** (as before, `ti64app.raster` / `+ti64/hexRaster.m` from EBSD_part2.m): every measurement is a rectangle
  one step wide and one row high; odd rows are shifted by half a step. Exact at 1 px per measurement (native export),
  but at high zoom the cells look like bricks with straight vertical edges.
- **Hexagons**: the cell of a measurement is its Voronoi cell on the hexagonal grid = a regular hexagon of width dx
  (flat sides left / right, points up / down, height 2 dx / sqrt(3)), neighbouring rows offset by half a cell. A
  pixel shows the nearest measurement centre; it is always the brick owner of its own row or of the nearer adjacent
  row (the farther rows are > dy away), so the rasteriser needs two lookups per pixel and no search.
- **Where:** the viewer draws hexagons when a cell is >= 8 screen pixels (zoom indicator: "brick" / "hexagons", with a
  tooltip explaining both); exports at native and 2x stay brick (documented in the sidecar `cells`), 4x and 8x draw
  hexagons with SQUARE pixels of dx / scale (regular hexagons need square pixels; the sidecar records the pixel size).
- **Each backend draws its own cells** (new protocol method `cell_image`): pymtex with the Python hexagon rasteriser
  (`raster.hexagon_image`), MATLAB with `+ti64/hexRasterize.m` (same rule, vectorised in chunks; called by
  `+ti64/hexImage.m` for pixel-exact exports). MATLAB figures (`render.m`, width mode) switch from the brick
  hexRaster image to hexRasterize when the figure has >= 4 output pixels per measurement; pymtex figures already
  use pymtex's raster, which picks the nearest measurement (hexagons at that resolution).
- **Test:** `tests/unit/test_hexagons.py` exports an 8x crop with a unique colour per measurement in each backend and
  checks the cell shape (pointed top / bottom, height 2/sqrt(3) x width), the half-cell offset of neighbouring rows
  and that no vertical edge runs through two rows; MATLAB and Python rasters agree to < 0.2 % of the pixels
  (ties on cell borders).

## D21 (2026-11-06) Stitching after the user test: stitch sets, manual arrangement, no ghosting
- **Stitch sets.** A folder is often a magnification series (JU04: 23x ... 50000x). Images of different
  magnification, pixel size or image size are NEVER stitched together (the `allow_rescale` option is removed).
  `stitch_sets` groups a selection by detector, magnification, pixel size, image size, rotation / tilt and then by
  overlapping stage footprints (field of view around the stage position, union of overlapping pairs). The GUI lists
  the sets (single images listed as not stitchable) and stitches one set; a mixed selection is refused with the list
  of sets.
- **Manual pre-arrangement.** Arrange view: thumbnails at the stage positions (default JSM-IT500HR mapping) or in a
  row without stage data; drag tiles, zoom, lock, select (click, Shift / Ctrl-click, Shift-drag box). "Refine" passes
  `manual_layout` (x, y px per tile), `locked` and `refine_radius` (default 10 % of the tile size): pairs are searched
  only within that radius around the given positions (the stage prior is replaced), locked tiles get a 1e3 prior
  weight (1e-4 for the others) in the global least squares. The layout is part of the recorded parameters (replay).
- **Ghosting.** Feathering over the whole overlap blends two slightly misregistered copies -> double edges. Now:
  (a) every used overlap is registered again AFTER placement (subpixel phase correlation of the two placed tiles):
  residual misalignment per pair and max / rms in the sidecar, mean |A - B| vs the pixel noise, and a
  difference-image view (Overlaps tab); (b) tiles are placed at their subpixel positions: integer origin floor(q)
  plus a cubic-spline resampling of the fraction (no rounding of origins); pairs are refined at full resolution with
  subpixel phase correlation (upsample 20); (c) blending only in a narrow band around a seam (`blend="seam"`,
  default, `blend_band_px` 32): the seam is the midline of the overlap (pixel owned by the tile whose edge is
  farthest) or a minimum-difference path (`seam="min_difference"`, dynamic programming on the smoothed |A - B|
  among the pixels the two tiles own); weights ramp linearly over the band from a signed distance transform of each
  tile's region. `blend="feather"` keeps the old whole-overlap blending, `blend="none"` raw pixels at integer
  positions. Accumulation arrays are disk-backed (RAM limit).
- **Test:** synthetic tiles with subpixel offsets and noise: residual misalignment < 0.15 px, |A - B| in the overlaps
  < 1.6 sigma, and the mosaic differs from the ground truth in the overlaps by less than the noise (no double edges).

## D22 (2026-11-06) GUI changes after the user test of phase 6.6
- **View tab** (Files | View | EBSD | Crack | Stitch): images, mosaics and .up2 files; image tiles from a mean
  pyramid (`tiles.ImageLayer`) in the Leaflet viewer (um coordinates), metadata panel, draggable scale bar, Export
  (`sem.export` now also takes a single image). .up2: header table and a pattern viewer (one pattern read at a time,
  row / column, step to neighbours); indexing comes in phase 9.
- **Messages name the exact place** ("under 'Images of the set'", "on the 'prior β' tab"), never "on the right".
- **Stale-slot bug.** Clicking a button inside a list row also selected the row, which redrew the list and deleted
  the button while the Windows dialog was open; the handler then failed ("parent element ... deleted") before
  refreshing (Relocate, New sample for this file). Fixes: row clicks ignore clicks on buttons, and handlers that
  await a dialog or a long step run in the page's root slot (`state.stable`).
- **Find missing files**: a folder is searched recursively by name (size first when several match) for all missing
  files at once; the matches are listed and relinked together.
- **EBSD tab**: map tabs "α map" | "prior β" | "Results"; each map tab has its own layer, overlays and history (the
  prior-β history = the reconstruction and the steps that use it); the reconstruction focuses the prior-β tab, which
  shows no boundaries by default. History is a panel right of the map; steps can be dragged into a new order
  (`ti64app.reorder`: dependency rules per operation, refused orders say why; a valid order re-runs the map's steps
  from the first changed position, the old ones stay as stale, automatically resolved step references are resolved
  again, and the whole reorder is one undo entry via `history.group`). Overlay menu: columns (grows to the right),
  colour and width factor per boundary type (`OverlayStyle`, also used by exports).
- **Legends on the map (what you see is what you get)**: scale bar, colour bar / colour legend, IPF key are boxes on
  the viewer (toggle, drag, resize); their positions (fractions of the view) go into export.map / sem.export
  (`legends`), which draw the bare map and add the items at the same relative place and size (PNG / TIFF burnt in,
  SVG embedded images, PDF overlay page with pypdf, new dependency). The export dialog no longer has legend options;
  it shows the final size in px, the physical size in um and an estimated file size, and takes a typed width or
  height in px (aspect locked).

## D23 (2026-11-09) Loading without sample / build direction; what a sheet change makes stale (fix 2.1)
- **Load with whatever is known.** `SampleSheet.specimen_id` and `build_direction` are optional; the input state
  falls back to the suggestion from the file name (CLAUDE.md: auto-suggested) and is NOT stored as if the user had
  entered it (`set_sheet` keeps `MapInfo.input_state = None` when the recorded value only repeats the suggestion).
  A linked `.ang` loads, cleans, segments, reconstructs and gets grain sizes without a sample or build direction.
- **Asked only when a step needs it.** `sample.require_build_direction()` raises `SheetIncomplete(map_id, missing,
  needed_for)`: IPF colours along "build" (figure, viewer, export, recon figures, IPF key), the prior-β width /
  length histograms. The GUI shows it as a warning with an **Open map sheet** button (`/files?sel=<map>`: Files tab,
  that file selected); never as an error with a log. Steps that can do most of their work without it (recon.run
  grain shape, grainsize.run prior-β calipers / intercepts along BD) run, leave the BD part out, put
  `"needs": ["build direction"]` in their summary and the GUI says what was left out, with the same link. The build
  direction is still never guessed. The EBSD tab shows "build direction not entered" and a "Map sheet" button.
- **Stale rule** (`sample.stale_on_sheet_change`, used by `Project.update_file` / `assign` / `save_sample_info` and the
  recorded `sample.set`): map file or input state changed -> everything of the map (the cleanup preset depends on
  it); build direction changed -> only `grainsize.*` and steps whose parameters use direction "build"; position / 4PB
  -> nothing yet (no step uses them). Entering the build direction after loading keeps the loaded map, grains and
  reconstruction.
- MCP: `set_sample_sheet` needs only `map_file` + `input_state`; the instructions say BD / specimen / 4PB come from the
  user and are only needed by the steps that use them.

## D24 (2026-11-09) EBSD tab after the second user test (fixes 2.2-2.12)
- **Auto-load** (2.2): opening the EBSD tab or picking another map in "Sample / file" loads it (progress bar under the
  edit menu, placeholder "Loading ..." on the map); "Load again" stays in "Load and cleanup". **Loaded-map cache per
  backend** (CLAUDE.md): `ebsd.load` stores the transferred arrays in `<project>/cache/maps/<backend>_<key>.npz`
  (key = file fingerprint, input state, setting, backend + versions); a later recorded load of the same file takes its
  outputs from there (metric `cached`); the backend object is made only when a later step needs it (`materialize`
  sets `OpContext.need_object`, which bypasses the cache). "Clear cache" in Settings removes it.
- **Only offer what exists** (2.8, `layers.available`): map list from the data actually stored by the source step
  (`stored_data`: CI / IQ present, Fit present and not constant, PRIAS columns in the .ang rows) plus KAM / GROD / GOS /
  GND only after they were computed; overlays: α-grain boundaries after grains.calc (α map tab only), prior-β
  boundaries after recon.run (prior β tab only), crack only when a crack path is drawn; the Overlays button is
  disabled with an explanation when there is nothing. Reconstruction figures appear only after a reconstruction,
  grain-size figures only after a grain-size step, and only the kinds of the current map tab.
- **Options hide their parameters** (2.7): wild-spike angle, smoothing-filter strength / limit, KAM order / cutoff vs
  GND stencil / Poisson's ratio, refined-OR limit (only for "refined OR if <= limit"), custom IPF vector (only when a
  direction is "custom"), histogram bin width (not for the colony map).
- **Prior-β tab** (2.9): its overlay menu offers the prior-β grain boundaries of the PARENT grains (`beta_gb`, from
  recon.run) and crack paths, not the α-grain boundaries; they are shown by default (this replaces "no boundaries by
  default" of D22, which hid the α boundaries). **Grain size works on the map tab it is started from**:
  `grainsize.run target = "alpha"` (α grains / laths / colonies; an existing grains.calc with the chosen boundary angle
  is reused instead of segmenting again) or `"prior_beta"` (prior-β grains of the newest reconstruction: ECD, calipers,
  intercepts across / along BD; no α segmentation needed); `"all"` (default, MCP / CLI / old recipes) as before. The
  section title says which ("Grain size · prior-β map"); the History panel lists prior-β grain-size steps on the
  prior β tab; `grainsize.figure` picks the newest grain-size step that has the part it draws.
- **Histogram bin width** (2.10): `grainsize.figure bin_width_um` (µm; empty = Freedman-Diaconis as before, at most
  2000 bins); the log-normal fits are unchanged.
- **Boundary lines keep their screen width** (2.11): overlay tiles were already drawn per zoom level with the width in
  screen px, but during a zoom animation Leaflet stretched the old tiles (lines 2x / 4x thicker or thinner until the
  new tiles arrived). Overlays are now hidden from zoomstart until the new level's tiles have loaded. Exports use the
  figure line width (Settings) x the per-type factor (unchanged).
- **Legends anchored to the map** (2.12): `LegendItem.frame = "map"`: x, y, w are fractions of the whole map (or
  image / mosaic) extent, the boxes are placed with `latLngToContainerPoint`, move with pan and scale with zoom (all
  sizes - text, padding, bar - follow the box width; the scale-bar length stays a nice number of µm). Exports convert
  them to the exported region (`legends.in_region`: whole map unchanged; a ROI / view keeps each item at its place on
  the map, items outside the region are left out). Items saved before (frame "image") are treated as before.

## D25 (2026-11-09) Recipe: replay speed, progress, delete steps (fixes 2.3-2.5)
- **Profile** (`scripts/profile_replay.py`, the user's test project: 48 steps incl. stale, MATLAB- and pymtex-recorded,
  replayed on pymtex). Before: the Python process grew to 6.5 GB (every backend object of every step stayed alive in
  `service.SESSION`; 1 GB per loaded full map) and the replay died with MemoryError at step 38 (export.map); under that
  memory pressure recon.run took 31-37 s (20 s normally) and grainsize.run 62 s (31 s); after each step with
  MATLAB-recorded outputs ~13-15 s went into comparing 2.6 M-point arrays value by value although another engine never
  reproduces them. After: 451 s for all 48 steps, RSS <= 0.6 GB at the end, compare 0 s for other-backend outputs.
- **Fixes**: run_recipe releases the backend objects a later step no longer uses (last use from `recipe.references`;
  `materialize` rebuilds one if needed); replay releases the scratch project at the end; after every recorded GUI step
  the objects of stale steps are released (`release_stale`). Outputs of a step replayed on another backend than
  recorded are `other_backend` (not compared value by value; backends are compared by tests/conformance), still "ok".
  Stale steps are only replayed when "include stale steps" is ticked (default off). Remaining hot spots: recon.run
  ~25 s per full map, 4x / 8x hexagon exports ~20 s, grain-size tables (CSV + XLSX via openpyxl) ~29 s to write and
  hash on the full map.
- **Progress** (2.4): replay and "Run a recipe file" show "step 3 of 9: Grains and boundaries (pymtex)", a progress
  bar, the elapsed time and Cancel (`run_recipe(on_step, should_stop)`; the running step finishes, then it stops; the
  report says "cancelled"). The result lists the run time and the slowest steps.
- **Delete steps** (2.5): History panel "Delete all steps…" and, when there are stale steps, "Delete stale steps (n)…",
  for the map tab shown or (tick) the whole project; a confirmation says how many and why. `service.plan_delete`: steps
  that use a deleted step are deleted with it; "stale" keeps stale steps that current steps still use. One undo entry
  (`history`); output files stay (undo needs them).
- **MATLAB restart** (fix 2.6, D26): a step that hits `EngineRestarted` is run once more after the restart (its sources
  are rebuilt by `materialize`); the GUI shows the plain-language notice.

## D26 (2026-11-09) MATLAB engine: own headless process, crash detection, one restart (fix 2.6)
- **Crash after the user test** (`matlab_crash_log.txt`, not committed): MATLAB R2025b aborted with "Breakpoint" in its
  desktop clipboard service (`EditorServices::ClipboardImpl` -> `libmwfoundation_json.dll`), not in ti64 or MTEX code.
  MATLAB's crash mode "continue" can leave a crashed process alive, so a call could hang.
- **Start**: always a new MATLAB process via `matlab.engine.start_matlab("-nodesktop -nosplash -logfile ...",
  background=True)` with a time limit (240 s, `TI64_MATLAB_START_TIMEOUT`). The app never attaches to the user's open
  MATLAB (no `connect_matlab` / `find_matlab` / shared engines).
- **Logs**: `%APPDATA%\ti64app\logs\` holds `matlab-engine-<python pid>.log` (MATLAB command window), MATLAB crash
  dumps (`MATLAB_LOG_DIR`), `ti64-gui.log`, `ti64-cli.log` and `ti64-mcp.log` (`ti64app.logs`). Crash dumps and logs
  are git-ignored.
- **Watchdog**: every engine call runs in the background; every 2 s the app checks that the MATLAB process (PID + start
  time) still exists and that no crash dump of that PID has appeared. An `EngineError` with the process gone (5 s
  grace), a missing process or a crash dump counts as a crash: the process is killed, a new engine starts once and
  `EngineRestarted` is raised with a plain-language message naming the log or crash report. Objects of the old session
  are lost; `handles()` lists only the new session's objects, so `service.materialize` rebuilds what a rerun needs.
  `service.run` runs the step once more after a restart and the GUI shows the notice. An `EngineError` while MATLAB is
  still running is passed on unchanged.
- **Notification buttons**: "Open log" / "Show" / "Open map sheet" now use `ui.notification` (the element converts
  `:handler` strings to functions); with `ui.notify` the buttons did nothing.

## D27 (2026-11-09) .up2 overview map and click-to-pattern (View tab, fix 2.13)
- One streaming pass over the .up2 (batches of ~16 k patterns, next batch read in a second thread; never loaded fully,
  peak RAM 1.6 GB) computes per pattern: pattern quality = Krieger Lassen (1994) frequency-inertia IQ as in kikuchipy
  (`get_image_quality`; equal to kikuchipy to 1e-4 without correction), AFTER static background subtraction (mean of
  4096 patterns spread over the file) and dynamic background removal (Gaussian high-pass, sigma = width / 8). Without
  the correction the raw EDAX patterns (~32 000 counts of smooth background) give an IQ map of the beam position on the
  detector, not of the microstructure. Virtual BSE = mean intensity of the whole pattern.
- Subsampling: every n-th scan row is read (rows are contiguous; skipped rows are never read), every n-th pattern of a
  row evaluated. Default n = 2 (1 um per point on the 0.5 um DED map); n = 4 quick, n = 1 full. Ti64_DED_Area 2
  (43 GB, 6.0 M patterns): n = 4 20 s, n = 2 61 s, n = 1 227 s (disk-bound).
- Cache: `<project>/cache/up2_overview/<stem>_<sha1(abs path, size, mtime_ns, algorithm version)>_s<n>.npz` (IQ,
  vBSE, mean pattern, rows done). Cancel keeps the rows done; the next pass resumes. A viewer cache like the tiles, not
  a recipe step; MCP `up2_overview` uses the same cache.
- Grid from the .up2 header (columns, rows, step, hex flag). Display: percentile stretch, optional "flatten shading"
  (display only). A click (probe) reads only the pattern under the cursor: scan row / column, x / y in um, pattern
  index, intensity range, IQ; the pattern can be shown background corrected.

## D28 (2026-11-09) Stitching after the second user test (fixes 2.14-2.18)
- (a) **One "Stitch" button**: registration plus refinement within the search radius (`refine_radius`, % of the tile
  size, always recorded). Positions come from the Arrange view once a tile was moved, else from the stage.
- (b) **Unknown stage axes** (`stitching.stage_analysis`): an axis with no stage spread (< 0.5 um) is unknown; when
  different images share an identical stage position (1 um stage resolution) although both axes vary, both axes are
  unknown. JU04: stage Y = 0.237 mm in all 20 images, images of one column have identical positions and beam shift.
  Pairs whose recorded axis allows an overlap are matched over the full range (coarse phase correlation + NCC of the
  whole tiles); the image direction and sign of the recorded axis are chosen by the summed scores of agreeing matches;
  matches must agree with it within max(search radius, 1 um); the threshold uses the coarse score; matches are refined
  at full resolution and placed by least squares with a 1e-9 prior. The stage -> mosaic transform fits recorded axes
  only. The Arrange preview spreads identical positions along the unknown axis in image-number order. The GUI says so
  under 'Stitch sets' (axis named, repeats listed) and in the Result notes; also in the MCP outputs.
- (c) **Repeats**: identical stage position + magnification = possible repeat; confirmed when matched at < 5 % of the
  tile size, else reported as "not repeats" (JU04 9473/9474 and 9491/9492 are vertical neighbours).
- (d) Sub-pixel step: the phase-correlation shift is accepted unless NCC drops by more than 0.01 (NCC is flat on noisy
  tiles; before, correct 2-3 px shifts on 10000x images were rejected).
- (e) **sem.export**: size and memory estimate first (`export_estimate`: refused above 6 GB or for a classic TIFF > 4 GB,
  with 1/2, 1/4, 1/8 and BigTIFF offered; warning above 100 Mpx); export size as a fraction 1, 1/2, 1/4, 1/8 (box
  average) next to the px / mm fields; tiled BigTIFF with pyramid (hashed by bytes); legends composited per item box;
  with an info strip or data bar the view's scale bar is left out (one bar only, recorded in the sidecar). The failed
  220 Mpx export came from PIL's decompression-bomb check in `hashing.png_hash` (raised after the file was written);
  the hash now skips that check and converts in row bands, with an identical digest.
- Verified on JU04 "stich along crack path": 8 BSE tiles at 1500x, 9766 x 22452 px, residual misalignment <= 0.3 px,
  matching the manual arrangement; 10000x set 9475-9479 one continuous column (<= 0.4 px). 250x set: tile 9446 has no
  good match, stays at its stage position and the message says to drag it on the Arrange tab.

## D29 (2026-11-11) EBSD load time (fix 3.8) and load progress stages (fix 3.7)
- **Profile** (`scripts/profile_load.py`, full JU02, click -> first image, each mode in a fresh process): pymtex new
  project 6.9 -> 4.6 s; restart with a cleaned map and no viewer cache 9.1 -> 5.6 s; restart with the viewer cache
  2.3 s; MATLAB new project 22 s (engine start + MTEX ~4 s, load 9 s, transfer + outputs 5 s, IPF layer 3 s, tiles
  1.5 s). Table in docs/performance.md.
- **Cause of "JU02 takes very long" in the user's test project**: not time. `GuiState.set` crashed because
  gui_state.json held `layer.<map> = "phase"` (before the map tabs), so `show()` stopped after colouring and the map
  never appeared. `set` now replaces an old plain value on the key path by a dict.
- **Layer cache key = only what the layer is drawn from** (`layers.layer_deps`: newest cleanup / load or `source`,
  + the map.compute step for KAM / GROD / GOS / GND, the recon.run step for prior-beta layers, the grains.calc /
  recon.run step for overlays) and only the sheet fields that change colours (file, input state, build direction).
  Before, any new step of the map recoloured every layer, and in a new session that rebuilt load + cleanup.
- **Viewer colours from stored orientations**: when the source object is not alive, `figure_spec` (viewer only,
  step op "view") colours the IPF map with `ipf_colors_quat` on the stored quaternions through any live object of
  the map or its load step; prior-beta IPF the same. Identical colours (2 600 367 / 2 600 367 pixels). Recorded
  figures and exports still use the source object (inputs recorded as before). The IPF key uses any live object or the
  load step (only the crystal symmetry is needed).
- **MATLAB graphics start-up**: the first figure render of a new engine session (exportgraphics, print, getframe alike)
  takes ~46 s once; later ones ~1 s. Not avoidable from Python (a warm-up at engine start would delay the first load
  by the same time); the viewer therefore shows the map first and draws the IPF key legend afterwards.
- **Progress stages** (`ti64app.progress`, reported only inside a channel): "reading file" (ebsd.load, also from the
  loaded-map cache), "transferring data" (get_ebsd + writing the outputs), "cleanup" / "repeating cleanup" (an
  ebsd.cleanup run / rebuilt for a later step), "cleanup from cache" / "reading cached map" (layer read from
  cache/viewer), "colouring map", "building grid" (cell layout), "drawing" (layer ready, tiles requested).

## D30 (2026-11-11) Stitch and View after the third user test (fixes 3.6, 3.16, 3.17)
- **View: images open with the microscope info bar, not the app's scale bar** (3.6). A JEOL .bmp / .tif carries its
  own data bar (detector, kV, WD, magnification, scale bar, image number, date), so a second scale bar on the image is
  redundant. Default per image: info bar ON, app scale bar OFF; an image without a data bar (nothing to show below it)
  gets the app's scale bar instead, so there is always one scale. The user's choice is stored per image / mosaic
  (`gui_state.view_opts_src[<source>]`); the old project-wide `view_opts` is no longer read. The export dialog gets
  `view_state = {info_strip: shown, overlays: False, annotations: False}` (what you see is what you get).
- **Stitch result zoomable** (3.16): the Result tab shows the mosaic in the Leaflet map viewer instead of a 2048 px
  preview. Tiles come from `gui/mosaic_tiles.py` (`/api/ti64/mosaic/...`): the pyramid levels of the mosaic TIFF are
  read lazily (TIFF tile offsets + `TiffPage.decode`, decoded tiles in an LRU of 384), the level whose pixels are just
  finer than the screen pixels is used; the info strip rows are left out. 22 753 x 9 679 px: opening < 0.01 s, a tile
  2-4 ms, RAM a few tens of MB. Mosaics without a pyramid (<= 8192 px) are read whole into `tiles.ImageLayer`.
  Overlays (ordinary `layers.Overlay` through the overlay route of tiles.py): **tile borders** at the final positions,
  coloured by best match score as before, with labels "image number · score"; **seams** = boundaries between the tiles'
  owner regions after the seam choice (midline or minimum-difference), extracted from the owner map during rendering at
  a stride <= 4096 px and saved as output `seams_<DET>` (npz, mosaic px). Steps from before have no seams output: the
  midline seams are computed from the tile positions for display and the line under the map says so. A click on a tile
  selects it (outline) and opens nudge / exclude as before. Choice of overlays remembered per project.
- **No stage-axis warning, no automatic full-range search** (3.17). D28 (b) is withdrawn: the JU04 crack path had a
  faulty stage (Y constant, identical positions), not a rule to detect. `stage_analysis` keeps only identical positions
  and possible repeats (D28 (c)); a constant stage axis is a normal single row / column again. Matching with the stage
  is the usual search within `search_um` around the stage prediction.
- **"Do not use stage positions"** (`sem.stitch use_stage=False`, default True; Stitch tab checkbox under 'Stitch
  sets', MCP `stitch_images use_stage`, CLI `--no-stage`): every pair is matched over the full tile (coarse phase
  correlation + NCC), good coarse matches (coarse NCC >= min. score) are refined at full resolution, a **maximum
  spanning tree** of the strongest matches is built first and the other matches are kept only if they agree with it
  within max(4 x coarse factor, 2 % of the tile) px, then the global least squares (first tile of each group fixed).
  Without the tree the false matches of repetitive α-lath texture (coarse NCC 0.6-0.8) outvoted true ones and the 0.99
  match 9465/9466 was dropped (mosaic 10 291 x 12 796 px, 17 px misalignment); with it JU04 crack path (8 tiles, 1500x)
  gives 9766 x 22 452 px, misalignment <= 0.26 px - the manual result of D28 - in 97 s, no tile without a match. The
  same path is used without stage data / pixel size. Manual Arrange positions still win once a tile was moved. With the
  option, stitch sets are not split by stage footprint (`stitch_sets(use_stage=False)`), the Arrange view starts from a
  grid in image-number order ('Grid' button), no stage -> mosaic transform is fitted (`stage_to_mosaic` = null) and
  repeats are confirmed / refuted by the matches as before.

## D31 (2026-11-11) Third user test: top bar, feedback, export log, EBSD tab (fixes 3.1-3.5, 3.9-3.15)
- **Memory in the top bar** (3.1, `sysinfo.py`): "App x GB · PC used / total", every 3 s; app = this Python process +
  the MATLAB engine process started by the app (its pid from the backend, never created just to ask); tooltip with the
  parts; orange above 80 % of the PC's RAM (`WARN_FRACTION`).
- **Feedback** (3.2, `feedback.py`, top bar button): category Bug / Idea / Question, description, app version, backend,
  OS, Python, RAM and the last 100 lines of ti64-gui.log with every absolute path (Windows, UNC, POSIX) replaced by its
  file name; URLs are kept. "Open GitHub issue" opens `<repo>/issues/new?title&body` (repo URL in Settings > Feedback);
  "Send e-mail" opens `mailto:` with the subject exactly "[Ti64] Bug" / "[Ti64] Idea" / "[Ti64] Question" (address in
  Settings, default vondrm34@cvut.cz) and writes `%APPDATA%/ti64app/feedback/ti64_feedback_<date>.zip` (report.md,
  log_tail.txt, screenshot.png only if ticked), selected in Explorer to attach. URL bodies carry only the last 25 log
  lines and are cut at ~7 kB (browser / mail-client limits); the .zip always has all 100. The window screenshot (PIL
  ImageGrab of this process's top-level window, DWM frame bounds) is taken when Feedback is pressed, so the dialog is
  not in it; off by default (issues are public); unavailable in browser mode.
- **Default export folder** (3.3): the Pictures known folder (`SHGetKnownFolderPath(FOLDERID_Pictures)`, follows
  OneDrive redirection), then the last folder used, per project (`gui_state.export_folder`).
- **Exports are not recipe steps** (3.4, CLAUDE.md updated by the user): `service.export(op, params, target)` runs
  export.map / sem.export unrecorded (no undo entry, no sample.set record), writes to `exports/<id>/`, copies file +
  sidecar to the chosen file and appends `{id, op, params, backend, versions, date, file, project_file, sidecar}` to
  `export_log.json` (`exportlog.py`). "Export again" (Export dialog > Earlier exports; MCP `export_again`) re-runs an
  entry with the same params and recorded backend. `service.run` of an export op is routed there, `run_recipe` turns
  export steps of old / hand-written recipe files into logged exports. **Migration** on open: export steps leave
  recipe.json and the undo snapshots and become log entries (`migrated: true`; the file the user saved to was not
  recorded before, so `file` is empty). Figure "Export" of a result is logged as `result.export`.
- **Export defaults = the view** (3.5): the Include boxes start from `view_state` (overlays shown, annotations, the
  microscope info bar shown) instead of the last choice; format / resolution are still remembered. The info-strip box
  reads "Add the microscope info bar under the image (detector, kV, WD, magnification, scale bar, image number, date)"
  with a tooltip.
- **Loading progress in the map area** (3.7): the GUI runs load / colouring in a `progress` channel (D29) and shows a
  bar, the current stage with elapsed seconds and the stages done with their times; the edit-menu bar is hidden then.
  The map is drawn before the IPF key (the first MATLAB render of an engine takes ~45 s).
- **KAM / GROD / GOS / GND in the map list** (3.9): always listed on the α tab once the map is loaded ("· computed on
  first use"); picking one runs map.compute with the section's parameters (α grains first with the 'Grains and
  boundaries' values if there are none). The section holds only the parameters (KAM order / cutoff, GND stencil /
  Poisson's ratio); Apply recomputes the map shown.
- **Prior-β grains** (3.10, user decision 2026-11-11: "segment prior-β grains"): 'Grains and boundaries' on the prior β
  tab runs grains.calc with `source` = the newest recon.run step: calcGrains on the reconstructed parent map (pymtex: the
  reconstruction's store entry is a map; MATLAB `+ti64/calcGrains.m` unwraps the reconstruction struct). Same
  parameters (HAGB / LAGB / alpha / min px). Its HAGB / LAGB overlays belong to the prior β tab (`layers.newest_grains`
  'beta', `BETA_OVERLAYS`), HAGB is switched on there and the tab stays on prior β. α steps (map.compute, grain size,
  α overlays) never pick prior-β grains (`ops_ebsd.resolve_grains`). Results appear on the tab they belong to (α grains
  -> α tab, reconstruction and prior-β grains -> prior β tab, KAM -> α tab, figures -> Results). Conformance on the JU02
  1000x1000 crop (ideal Burgers): grain counts, inner area-weighted ECD and HAGB length within 10 %
  (`tests/conformance/test_fix3.py`).
- **IPF key only on IPF maps** (3.11): the IPF-key button and box are hidden on other maps; an IPF key that is on is
  shown as the colour bar / legend of a scalar or ID map in the same place; boxes with nothing to draw are not shown
  (the placeholder texts "no colour bar ..." / "IPF key: only on IPF maps" are gone). Hidden items keep their settings.
- **Legend snapping** (3.12): `LegendItem.anchor` (tl / tc / tr / bl / bc / br), `margin` (fraction of the map width,
  default 2 %), `auto` (width = 22 / 30 / 18 % of the map width for scale bar / colour bar / IPF key). Dragging within
  28 px of a snap position snaps (blue outline); 'Legend layout' menu in the map toolbar sets position, auto size and
  margin; a manual resize switches auto off. The viewer writes the resulting x / y / w back, so exports need nothing new.
  New legends start snapped (scale bar bottom left, colour bar bottom right, IPF key top right).
- **History right-click** (3.13): Show parameters / Delete…; no dependents -> deleted at once; dependents -> "Delete them
  too" or "Keep them, mark stale" (`service.delete_step(dependents=...)`); one undo entry either way.
- **Results right-click** (3.14): figures (figure / recon.figure / grainsize.figure steps): Delete (the step, undoable),
  Save (editable: MATLAB `.fig` via `savefig` + `.svg` / `.pdf` vector; pymtex `.svg` (text as text) / `.pdf`), Export
  (png / jpg / tif / svg / pdf). Both re-draw the recorded step with the recorded backend and parameters
  (`service.render_figure_as`, `OpContext.figure_ext`; jpg from the PNG). MATLAB `+ti64/+plot/saveFigure.m` picks the
  writer by extension (render.m, renderChart.m, renderKey.m use it). Tables: "Delete from Results", Save = their CSV /
  XLSX / JSON folder in Explorer.
- **Histogram range** (3.15): grainsize.figure `min_um` / `max_um` (GUI "shown from / up to [µm]", MCP
  `grain_size_figure`): bins start at min and end at max (`hist_edges(lo, hi)`), the title says how many grains are
  outside; statistics, log-normal fit and area fractions remain those of all grains.
- **Viewer**: re-fits only when the extent really changes (each `set()` sent a new extent array and threw the user out
  of a zoomed view, found while testing the zoomable stitch result).

## D32 (2026-11-12) Fourth user test: APEX phases, PRIAS exports, hex .up2, reports for Claude (fix 4)
- **Phases by crystal symmetry, never by name** (`phases.py`): APEX exports call the phases "Ti (Titanium, alpha)" /
  "Ti (Titanium, beta)". The `# Symmetry` code of each header phase gives its Laue group (TSL codes: 62 = 6/mmm -> α,
  43 = m-3m -> β); both backends rename the phase at load to the canonical 'Ti (alpha)' / 'Ti (beta)' (pymtex
  `CSList[i].mineral`, MATLAB `ti64.loadEbsd` phaseItem array), so every later step finds the phases the same way.
  The original names stay in the load metadata (`phases`: id, original, Laue group, role, source). Two phases of the
  same role (e.g. α + Ti3Al, both 6/mmm) stop the load with a map-sheet message (`PhaseMappingAmbiguous`, a
  `SheetIncomplete`) until the user sets the role in the map sheet (Files tab, section 'Phases'; `MapInfo.phase_map`,
  MCP `set_sample_sheet(phase_map=...)`); changing it marks the map's results stale. Figures take the phase ids from
  the load metadata instead of assuming 1 = α, 2 = β.
- **MATLAB single-phase numbering**: MTEX 7.2 numbers the phase of a single-phase .ang 0 (notIndexed -1), pymtex 1;
  every alpha pixel of JU04_area3/4 and JU06_* came back from MATLAB as phase 0 = "not indexed". `loadEbsd` now sets
  `phaseMap = 0:numel(CSList)-1`, so both backends report the header phase numbers (identical per-pixel phases on all
  8 new maps).
- **Void fill bug (both backends)**: `calcGrains(..., alpha = 0)` in MTEX 7.2 and pymtex 1.0 leaves large
  not-indexed regions WITHOUT a grain (grain id 0); the cleanup looked up their size as 0 and filled them as if they
  were <= 4 px voids. On JU04_area1 (raw, 78 % CI < 0.1) 191 543 px were "small voids" and the whole map was
  extrapolated from 22 % of the points. Not-indexed pixels without a grain now get the size of their connected
  not-indexed region (`hexgrid.component_sizes`, MATLAB `conncomp`); only regions <= noise_max_void_px are filled.
  Maps with few not-indexed pixels (the OIM-cleaned test maps) are not affected.
- **Raw preset on the new raw maps** (CI < 0.1 removed, then EBSD_part2 noise cleanup), both backends identical:

  | map | step | CI < 0.1 | indexed after cleanup |
  | --- | --- | --- | --- |
  | JU04_area1 | 0.08 µm | 78 % | 20.5 % |
  | JU04_area2 | 0.15 µm | 63 % | 36.2 % |
  | JU04_area3 | 0.30 µm | 43 % | 58.4 % |
  | JU04_area4 | 0.20 µm | 17 % | 86.8 % |
  | JU06_compression / tensile / tensile_mid / tensile_crack | 0.30 µm | 20 / 18 / 11 / 21 % | 85.8 / 88.8 / 95.3 / 83.8 % |

  OPEN (user decision): whether CI 0.1 suits the fine-step raw APEX maps JU04_area1/2 (median CI 0.018 / 0.036).
- **PRIAS text exports** (`io/prias.py`): `<map>_prias.txt` (also `.txt.txt`) next to the .ang, channel from
  "Column 3: Grayscale value - PRIAS Top Strip" (top / center / bottom). Points are matched to the .ang grid rebuilt
  from its header (row from y, column from x with the hex half-step; origin = first data row) within half a step (the
  export rounds y to 3 decimals); a point off the grid, two points on one map point or a different point count give a
  plain-language PriasMismatch. All 8 exports match (0.1-1.6 s, cached in `cache/prias/`). Layers / figures
  `prias_top`, `prias_center`, `prias_bottom` ('prias' = first channel); PRIAS exports are sidecars of their .ang on
  Add file; `modules/registration.reference_values` = PRIAS (top, centre, bottom) else IQ, for phase 8.
- **Hex .up2 with padding** (`io/up2.py`): header byte 16 = 'extra patterns' flag; with it a hex file holds
  n_rows x n_cols patterns, one padding pattern per short row, at the END of the row (full-resolution test in the
  module doc: odd rows r 0.564 vs 0.367 with the padding assumed at the start, JU04 Area 2; 0.552 vs 0.412 Area 1).
  The old reader counted n_cols - 1 per short row: r ~ 0.0, the diagonal seam. Point-order access for PyEBSDIndex
  (`iter_point_chunks`) and kikuchipy (`point_array`, lazy dask, 1-D navigation).
- **Scan-ID pairing**: bytes 42-57 = scan GUID (little-endian GUID layout) = `# SCANID` of the .ang
  (`find_paired_ang`: project files first, then the .up2's folder). View tab: 'paired map' in the metadata, IQ / CI /
  PRIAS of the paired .ang on the same cells, the comparison line (high-passed pattern quality vs IQ on points with
  CI >= 0.1, r and best shift; JU04 Area 1 / 2 at step 2: best shift (0, 0)). Note: band-free regions (pores, the
  non-diffracting area) score HIGH in the frequency-inertia pattern quality but low in OIM's IQ.
- **Hexagonal cells in the View tab**: the .up2 overview is a `layers.Layer` on a hex `CellLayout` (same tile
  renderer as the EBSD viewer: brick when zoomed out, hexagons at >= 8 px per cell). Subsampling keeps a hex grid:
  every other kept row starts step/2 points later (overview cache version 3).
- **Feedback**: e-mail removed (button, setting; old `email` keys are ignored). **Save report for Claude**
  (`report.py`, Help menu '?', Feedback dialog, Ctrl+Shift+R captured before WebView2 reloads the page):
  `reports/YYYY-MM-DD_HHMM_<category>.md` + `.json` (+ `.png`), sections in the order requested; breadcrumbs
  (`breadcrumbs.py`: clicks via a capture-phase JS listener, tabs, dialogs, recipe steps from `service.run`, errors
  from a root-logger handler with full tracebacks), paths shortened to file names.
- **Stitch**: one Export button (next to 'Stitch'); with BSE and SE mosaics it asks which one.
- **Tests adjusted**: JU02 crop raw-preset reference counts (35 px of larger voids no longer filled: small voids 841,
  filled 1030, unfilled 22; identical in MATLAB); phase-2 conformance boundary lengths allow 1 % of a step per
  not-indexed pixel (RI02 crop now keeps 224 more not-indexed px; calcGrains' alpha fill next to them differs, D7);
  cross-backend replay expects `other_backend` (fix 3 behaviour, the test was stale).
- **Data note**: the JU06_tensile PRIAS export has a step in its own values at row ~429 (row means 757 -> 744), visible
  as a band at the bottom of the PRIAS map; it is in the exported text, not from the matching.

## D33 (2026-11-13) Fifth user test: orientation check vs OIM, cleanup as steps, tuning, badges (fix 5)

- **Orientation check vs OIM** (docs/orientation_check.md):
  - The JU04_area4 probe point and map match OIM Analysis in both backends: not mirrored or rotated, crystal frame
    X||a.
  - Setting 2 = the file orientation rotated 180° about (1,−1,0)/√2, so app x = −A2, y = −A1, z = −A3.
  - Nothing changed at load. The `CartesianAlignment` line and the backend's crystal frame are recorded in a new
    `frame` output of `ebsd.load` (not in `meta`, so the recorded load outputs and replays stay identical), with a
    warning when they disagree.
  - Load cache version 3 (frame sidecar).
- **Cleanup = separate operations** (`ebsd.clean`, `operation` + `params`; `base.CLEAN_OPS`; pymtex
  `clean_step`, MATLAB `+ti64/cleanStep.m`):
  - Operations: ci_threshold, spike_removal, noise_removal (with fill of the removed points), void_fill,
    grain_dilation, smoothing.
  - Each step works on the CURRENT map of the map id: `recipe.current_map_step` = the newest non-stale load /
    cleanup step in recipe order. So a 'Load again' after cleanups starts from the loaded map; before fix 5, the
    newest cleanup was preferred over a newer load.
  - Void fill now uses the connected not-indexed region size (`component_sizes`) instead of the alpha = 0 grain of
    the void; that was the same number, except for grain-id-0 regions, which already used the component size.
  - The default chain (raw: CI 0.1 → noise → void fill) is close to, but not bit-identical with, the old combined
    `ebsd.cleanup`: there, voids were measured before the noise removal and filled together with it. `ebsd.cleanup`
    stays for existing recipes; replay is exact.
- **Grain dilation, OIM style** (tolerance 5°, minimum grain 2 px, multiple rows, single iteration):
  - Points outside grains of at least the minimum size (calcGrains alpha = 0 at the tolerance) take the orientation
    and phase of a neighbour in the neighbouring grain with the most neighbours; ties go to the first neighbour in
    the fixed order W, E, NW, NE, SW, SE.
  - Sweeps are decided from the state before each sweep (same rules as the fill, `hexgrid.dilation_sweeps`, numba;
    MATLAB `dilationFill` with maxIter), and the number of changed points per sweep is reported.
  - A dilated point copies a neighbour's orientation, not the grain mean.
  - Conformance: tests/conformance/test_fix5.py on JU04_area1.
- **Tuning replaces** (`service.apply`, used by the GUI and the MCP analysis tools; `run()` and replay
  unchanged):
  - When the newest step of the map (sheet records aside) has the same `tune_key`, the new step takes its place in
    the recipe, keeping the replaced step's source. `tune_key` = op + cleanup operation / map kind / grain-size
    target / alpha vs prior-beta grains.
  - Undo restores the previous parameters. Never for sheets, annotations, crops or comparisons, nor for a step
    another step uses.
- **Badges** (`badges.py`):
  - No badge while a section was never used for the map.
  - 'changed, not applied' only when the fields differ from the recorded parameters of the section's last applied
    step.
  - 'applied HH:MM' when that step is current.
  - 'stale' when it is marked stale or its source map / grains / reconstruction is no longer the current one.
  - The old rule (any draft vs the defaults) showed 'not applied' on Grain size without a change (report 0919).
- **Indexing label** (`MapInfo.indexing`, `indexing_asked`):
  - The question is asked once per file whose name contains 'rescan' or whose header has the older OIM phase names
    'Ti (alpha)' / 'Ti (beta)'.
  - It is metadata only: no sheet record, nothing stale. It appears in reports, the export sidecar
    (`indexing`) and MCP `get_sample_sheet` / `set_indexing_label`.
- **Tooltips**: 400 ms delay, hidden on mouse-down, key press and window blur, and before the report screenshot
  (the Feedback tooltip was in every screenshot because it was the hovered button).

## D35 (2026-11-13) Phase 6.7: texture and Young's modulus from texture

- **Elastic constants (user decision 2026-11-13).** Values were read from C:/Claude/literature; presets are
  editable in Settings (`ti64app/elastic.py`), and every elastic step records the constants it used.
  - α default: Fisher & Renken 1964, pure α-Ti at 298 K, doi:10.1103/PhysRev.135.A482: 162.4 / 180.7 / 92.0 /
    69.0 / 46.7 / 35.2 GPa (C11 C33 C12 C13 C44 C66; C12 = C11 − 2 C66).
  - Other α presets:
    - Rossin 2023 (EBM Ti64, RUS, standard beam energy), doi:10.1016/j.ndteint.2023.102803.
    - Wielewski 2017 (Ti64, lattice-strain PF), doi:10.1016/j.actamat.2016.12.026, from Table 1 'Optimized' of
      the paper: C11 169, C12 89, C13 62, C44 43 GPa; C33 = 196 from their constraint C11 + C12 = C13 + C33,
      C66 = (C11 − C12)/2 = 40. Rossin 2023 (ref. 36) quotes C12 and C13 swapped; the original was used.
  - β default: Ti15Mo solution-treated single crystal (Olejňák 2023, doi:10.1016/j.matdes.2023.112474), a stand-in;
    the literature folder has no constants for the V-rich β of Ti64. Other β preset: LCB from the same table.
  - Kovarik 2022b has only polycrystal E of cold-sprayed Ti64 (80–101 GPa).
  - Kovarik2023_HSfit.pdf is about fatigue crack growth, not Hashin–Shtrikman bounds; HS bounds are skipped for now.
- **Frame.** Orientations are rotated from the map frame into the specimen frame by the map sheet position
  (map +x / +y = specimen axes), g_specimen = R g_map; BD comes from the sample sheet (specimen frame) or the map
  sheet. Without a position, the map frame is kept (axes x / y / z) and the result says so (`ti64app/texture.py`).
  'All maps of a sample' needs every map's position.
- **ODF.**
  - Both backends: calcDensity, de la Vallée Poussin kernel, halfwidth 5° by default, area weights. Per pixel uses
    at most 400 000 random points (fixed seed); per grain uses mean orientations with grain-area weights.
  - Texture index = ‖f‖² (MATLAB: norm(odf)², since textureindex is deprecated in MTEX 7.2; pymtex: textureIndex).
  - PF / IPF densities are antipodal (mean of ±direction).
  - pymtex 1.0's radon broadcasts wrongly over many crystal directions, so IPFs use odf.inv().radon(r, h).
  - On JU02 (crop and full map) both backends agree: max MRD and texture index within 1 %, PF / IPF grids within
    2 % of the maximum (tests/conformance/test_phase67.py).
- **Figures.**
  - XRD_part2.m style: equal-area, upper hemisphere, one colour range per figure, turbo, specimen-direction labels
    (white background).
  - MATLAB writes a .fig next to every PNG.
  - E(direction): stereographic plot in the stereograph_settings.m style (dotted cross, decimal commas on the colour
    bar) and a 3D surface.
- **Elastic averages.**
  - The single-crystal stiffness and compliance are rotated per orientation by MTEX tensors in each backend and
    averaged with area weights.
  - Voigt = mean C, Reuss = (mean S)⁻¹, Hill = (Voigt + Reuss) / 2 (Hill 1952).
  - E(n) = 1 / (n n n n : S) of the averaged matrix. α and retained β are mixed by area fraction.
  - The elastic step refuses the prior-β map: room-temperature stiffness comes from α + retained β.
  - Porosity is ignored.
  - Bootstrap: grains resampled with replacement (default 200 runs, seed 0) on the backend's per-grain rotated
    tensors, giving the SD per direction.
  - Tests: random orientations reproduce the isotropic Voigt / Reuss / Hill moduli within 0.4 %; a single
    orientation reproduces the single-crystal directional modulus; Reuss ≤ Hill ≤ Voigt on a 31 × 121 direction
    grid.
- **Recipe.**
  - texture.run and elastic.run are recipe steps; Save / Export of their figures uses the result menu (texture.run:
    variant pf | ipf).
  - texture.export_odf goes to the export log (MTEX text + JSON sidecar), so it is listed under 'Earlier exports' and
    can be exported again.

## D36 (2026-11-17) Phase 6.7 fix 1: alpha-only maps, map axes, grain CI, figures as view state

- **Parent phase for alpha-only maps (item 1).**
  - Raw APEX exports of α-only scans (JU04_area3/4, all JU06_*.ang) have no cubic phase. Before the reconstructor is
    built, both backends add Ti (beta) (m-3m) to the map's phase list (pymtex: `with_parent_phase`; MATLAB 7.2:
    `CSList(end+1)` + `phaseMap(end+1)`, because CSList is a phaseItem array there, not a cell).
  - The lattice parameter is a setting (Settings > Parent β phase, default a = 3.32 Å as EBSD_part2.m). It is
    recorded in the step as `ReconParams.betaLattice`, so replay uses the same value. Only the symmetry matters for
    the reconstruction; the lattice is kept for completeness.
  - The reconstruction summary says `beta_added`. Retained β is 0 / "not in file" in grain size and elastic results.
  - Crystal symmetries are now read from the phase LIST, never from the phase's pixels (`phase_cs` / `ti64.phaseCS`).
    A crop or file without β pixels used to fail in IPF colours, ODFs and tensors.
  - On a JU04_area3 head crop (150 rows), pymtex and MATLAB agree: reconstructed fraction within 2 %, ≥ 97 % of the
    points in the same reconstruction state (tests/conformance/test_fix671.py).
  - Every .ang in data/ runs load → default cleanup → grains → reconstruction → grain size → texture (α, prior β) →
    elastic in both backends on a 100-row head crop (tests/integration/test_fix5_orientation.py).
- **Directions never fail (item 2) and map axes (item 9).**
  - The map axes are always available. In 'exact' mode they are called x / y / z without a position, or beam axis /
    thickness / width with one. In 'approximate' mode they carry the user's labels.
  - Directions that cannot be resolved yet (BD, beam axis, …) are listed with what to enter. The GUI greys them out
    with a tooltip; the ops skip them with a note. If none is left, the defaults (the map axes) are used.
  - Default directions: exact with a position = BD (else thickness), beam axis, width. Otherwise the three map axes,
    plus BD when it is not along one of them.
  - 'Approximate' does not rotate anything: the frame stays the map frame. The axis marked as BD becomes the map's
    build direction (a unit vector along that axis; the sign does not matter for axial quantities), so prior-β
    length / width and IPF colouring along BD use it.
  - The position ORIGIN stays visible in both modes, because the 4PB role uses it. The 'map +x / +y along' selects
    belong to exact mode only.
  - Changing the BD, the position or the axes marks texture / elastic results of the map stale.
  - Old recipes with direction names 'map x / map y / map z' still resolve.
- **Grain CI standardization (item 5).** Same grains as grain dilation (calcGrains, alpha = 0, tolerance angle,
  minimum size; no 'multiple rows' option). Every indexed point of such a grain gets the grain's mean CI (default) or
  max CI (as OIM). Points of smaller grains and not-indexed points keep their CI, and orientations are not changed.
  The report counts points whose CI changed (> 1e-9).
- **Dilation default (item 4).** `single_iteration` is on by default. Recorded steps store all their parameters, so
  old recipes replay unchanged.
- **Figures from results are view state (item 6).**
  - `grainsize.figure` is a view op: no recipe step and not in History. It is drawn by the backend that computed the
    grain size into `views/<id>/`.
  - `views.json` keeps the settings per map (the GUI fills its fields from them) and a registry of drawn figures, so
    Save / Export / Export again still work.
  - When a project is opened, old `grainsize.figure` steps are moved from recipe.json and the undo snapshots into
    `views.json`. This uses the same mechanism as exports in fix 3.4.
- **Log-normal fit (item 7).**
  - Off by default. The bars are the grains inside the shown range: without a range the automatic bins end at the
    99.5th percentile. Area percentages are of the shown area.
  - The fit is the MLE log-normal of the SAME shown grains. It is scaled so that its area inside the range equals the
    bars' area (pdf × n × bin / P(range)), and the title says "n of N shown".
  - The log-normal numbers in the grain-size summary on Results are shown only when the box is ticked. The CSV / XLSX
    tables always keep them.
  - The OIM grain-file comparison keeps its fit.
