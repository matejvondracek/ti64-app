# Prior-beta reconstruction method (from the Research Assignment)

Source code: `matlab_reference/EBSD_part2.m` (MTEX 6.1, last edited 2026-10-11; copied from
`vyzkumak/python/`). Reference outputs: `EBSD_data/JU/figures_prior_beta/` (JU02 map, final run
2026-10-11 10:01, 1.9 min). Reuse the MATLAB logic; do NOT reuse the old Python wrapper
(lib.py wrote a temp script, injected variables into the workspace and ran `clear all`).

## Pipeline

1. **Load**: `loadEBSD_ang(fname, 'setting', 2)` (OIM .ang, hexagonal grid).
   Plotting convention: `how2plot.east = xvector; how2plot.outOfScreen = -zvector`.
2. **Noise cleanup** (on ALL pixels, not `ebsd('indexed')`, so grains don't grow into voids):
   `calcGrains(ebsdRaw, 'angle', 3*degree)`; remove indexed grains with <= 2 px and
   not-indexed regions with <= 4 px; their pixels are refilled by neighbours in the next step.
3. **Alpha grains**: `calcGrains(ebsd, 'angle', 1.5*degree)`, then `smooth(grains, 5)`.
4. **Cache**: save `ebsdRaw, keep, grainIdKept, grains` to `<name>_mtexcache.mat` (-v7.3, no compression),
   keyed by file name + date + cleanup and segmentation parameters; reload when the key matches.
5. **Job**: `parentGrainReconstructor(ebsd, grains)`; `csParent` = beta CS from the file
   (if no beta is indexed: `crystalSymmetry('m-3m',[3.32 3.32 3.32],'mineral','Ti (beta)')`); `csChild` = alpha CS.
6. **OR**: `orientation.Burgers(csParent, csChild)`, refine with `job.calcParent2Child`;
   keep the refined OR only if it is <= 1 deg from ideal Burgers (JU02: 0.82 deg -> refined OR kept).
7. **Reconstruction** (`par.method = 'variantGraph'`, the one used):
   `calcVariantGraph('threshold', 5*degree, 'tolerance', 1.5*degree)`,
   `clusterVariantGraph('numIter', 3, 'inflationPower', 1.05)`,
   `calcParentFromVote('minProb', 0)`.
   Alternative `par.method = 'graph'` (older, not used): `calcGraph(2 deg, tol 1.5 deg)`,
   `clusterGraph(inflationPower 1.6)`, `calcParentFromGraph`, `calcGBVotes('p2c', 2 deg, 1.5 deg)`,
   `calcParentFromVote`. The log prints both parameter sets; only one method runs.
8. **Merge**: store the pre-merge state (`job.grains`, `job.mergeId`) so merge settings can be swept;
   `mergeSimilar('threshold', 3*degree)` once ('single' mode), or 'progressive' mode = steps from
   1 deg by 0.5 deg up to the threshold; then `mergeInclusions('maxSize', 5)`.
9. **Parent EBSD**: `job.calcParentEBSD` (per-pixel `fit` and `variantId`).

Thresholds follow Yang et al. 2025 (doi:10.1016/j.ijlmm.2025.07.009): reconstruction 5 deg (8 also ok),
merge 3 deg (their r5m3 / r8m3).

## Diagnostics (keep all of them)

- **Neighbour misorientation before merging**: misorientation between the beta orientations of
  neighbouring alpha grains, weighted by boundary segment length.
- **Merge sweep** (numbers only): configs {single 0.5, 3, 6, 8 deg; progressive 3 deg}; for each:
  grains with ECD >= 5 um, area-weighted ECD,
  *bridged steps* = % of boundary length with misorientation > 4 deg that ends up INSIDE a final grain (over-merging),
  *split noise* = % of boundary length < 1 deg that stays a final grain boundary (under-merging).
- **Summary** (`reconstruction_summary_latest.txt` via `diary`): OR deviation, sweep table, chosen setting,
  boundary-length share in classes 0-0.5-1-1.5-2-2.5-3-4-5-7.5-10-15-Inf deg, grains >= 5 um and all fragments,
  reconstructed alpha AREA fraction, mean fit, % pixels with fit > 3 deg, area-weighted mean ECD,
  mean fit and pixel share per variant, run time.
- **Run log**: one line appended to `reconstruction_log.txt` per run (all parameters + results).

## Figures (PNG at 600 dpi via exportgraphics, optional .fig)

| File | Content |
| --- | --- |
| parent_grains_IPFZ | prior-beta grains coloured by mean orientation (IPF-Z, beta key), voids white, prior-beta boundaries > 10 deg in black |
| alpha_IPFZ_with_prior_beta_boundaries | alpha IPF-Z pixel map with prior-beta boundaries (black) |
| diagnostic_fit_map | per-pixel fit to Burgers OR, parula, 0-5 deg |
| diagnostic_variant_map | variant ID per pixel (hsv, 12 or 24 colours) |
| diagnostic_neighbour_misorientation_hist | boundary-length share vs neighbour beta misorientation, 0.25 deg bins, log y, merge threshold line |
| diagnostic_boundary_misorientation_map | final boundaries light grey; pre-merge boundaries >= 1.5 deg coloured (turbo, 1.5-8 deg) -> coloured lines inside grey grains = bridged steps |
| parent_IPF_color_key | custom beta IPF key (same formula as HKLDirectionKey, rho wrapped/clamped to remove the blue sliver along [001]-[101]) |
| parent_grain_size_hist | ECD histogram, fragments < 5 um excluded |

Reusable helpers in the script: `hexRaster` / `hexRasterImage` (draws the hex grid as ONE image: each hex
pixel fills two half-width columns, odd rows shifted; ~2.6 M pixels render fast; use this for the GUI too),
`rasterMap` (MTEX axes + scale bar + image underneath + boundaries on top), `clipToMap`, `innerBoundary`,
`saveFig`, `doMerge`, `mergeMetrics`.

## JU02 reference results (regression test)

| Setting | beta grains >= 5 um | area-w. ECD (um) | recon. area | mean fit (deg) | fit > 3 deg |
| --- | --- | --- | --- | --- | --- |
| final: vgThr 5.0, single merge 3.0 | 47 (701 fragments) | 93.9 | 100.0 % | 3.38 | 50.7 % |
| vgThr 1.5, progressive merge 3.0 | 150 | 54.9 | 99.5 % | 2.13 | 20.3 % |
| vgThr 1.5, single merge 2.5 | 73 | 79.0 | 99.5 % | 2.95 | 40.8 % |

Merge sweep at the final setting: single 0.5 -> 762 grains, 7.9 um, bridged 0.7 %, split 81.4 %;
3.0 -> 47, 93.9 um, 39.8 %, 0.0 %; 6.0 -> 28, 107.4 um, 58.5 %, 0.0 %; 8.0 -> 23, 120.6 um, 63.6 %, 0.0 %;
progressive 3.0 -> 134, 63.4 um, 14.5 %, 0.0 %.

## Notes for the app

- Turn the script into functions in `matlab/+ti64/` with explicit inputs/outputs and a `par` struct
  (no workspace globals, no `clear all`); keep the default values above.
- A plain threshold graph (first attempt, 2026-10-04) reconstructed only 0.5 %; keep the variant graph as default.
- The final setting trades fit (50.7 % > 3 deg, 39.8 % bridged steps) for fewer, larger grains; progressive
  merging fits better with more grains. The GUI shows the sweep so the user decides per map.
- The refined OR gives 24 variant IDs instead of 12 (the script already handles `nV`); report
  variant-selection statistics against the 12 ideal Burgers variants.
