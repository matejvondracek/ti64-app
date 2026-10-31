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
