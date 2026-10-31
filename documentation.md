# Ti64 EBSD/BSE analysis app – documentation

State after phase 4 (2026-10-31). Details of decisions: `docs/decisions.md`; data facts:
`docs/data_audit.md`; timings and RAM: `docs/performance.md`; reconstruction method (phase 3):
`docs/beta_reconstruction_method.md`.

## Purpose

A local app for EBSD + BSE analysis of electron-beam-melted Ti-6Al-4V (alpha + retained beta):
orientation and quality maps, cleanup, grains and grain boundaries, prior-beta reconstruction, and later
grain-size statistics, 4-point-bending stress mapping and fatigue-crack analysis.

- One service layer, three front ends: a NiceGUI web page (for the user), MCP tools (for Claude) and a CLI.
- Two interchangeable analysis backends behind one interface, chosen in Settings:
  **pymtex** (MTEX for Python 1.0.0, default) and **MATLAB R2025b + MTEX 7.2** (optional).
  Each backend computes and draws its own figures; conformance tests check that both give the same numbers.
- Every action is a recipe step in `projects/<name>/recipe.json` (parameters, inputs, outputs with
  content hashes, backend, software versions), so any result can be replayed and checked.

## Running it

| What | Command (ti64 env: `C:\Users\matej\miniforge3\envs\ti64\python.exe`) |
| --- | --- |
| GUI | `python -m ti64app.gui` → http://127.0.0.1:8064 (or `ti64 gui`) |
| MCP server | `python -m ti64app.mcp_server` (stdio) |
| CLI | `ti64 backends`, `ti64 replay <project>` |
| Tests | `python -m pytest` (`-m "not slow and not matlab"` for the fast part); MATLAB: `runtests('matlab/tests')` |
| Test crops | `python scripts/make_crops.py` (300×300 and 1000×1000 points, into `data/test/crops/`) |
| Full-map check | `python scripts/run_phase2_full.py JU02 pymtex --angle 270`; reconstruction: `python scripts/run_phase3_full.py JU02 pymtex --angle 270 [--or-mode burgers] [--sweep]`; grain size: `python scripts/run_phase4_full.py JU02 pymtex --angle 270 [--hagb 5]` |

Machine paths (MTEX folder, MATLAB, literature) go in `settings.local.toml` (git-ignored, template
`settings.example.toml`) or `%APPDATA%\ti64app\settings.toml`.

## Implemented features

### Phase 0 – data audit and repo skeleton
- Audit of the three .ang maps and the .up2 header; pymtex vs MTEX load comparison (identical);
  reference-frame check against the bachelor thesis (`docs/data_audit.md`).
- Scripts: `scripts/audit_ang.py`, `audit_pymtex.py`, `audit_mtex.m`, `compare_loads.py`, `make_crops.py`,
  `bench_load.py`.

### Phase 1 – project model, loaders, backends, GUI/MCP
| Feature | Implementation |
| --- | --- |
| Project + recipe, replay | `project.py` (`Project`), `recipe.py` (`Recipe`, `RecipeStep`), `service.py` (`run`, `replay`, `verify_outputs`, `materialize`), `hashing.py` (content hashes, input fingerprints) |
| Backend interface | `backends/base.py` (`AnalysisBackend`, `LoadParams`, `EBSDData`, handles), registry `backends/__init__.py` |
| pymtex backend | `backends/pymtex_backend.py` (objects kept in-process by ID) |
| MATLAB backend | `backends/matlab_backend.py` (persistent engine, objects kept in MATLAB by ID via `matlab/+ti64/store.m`, arrays via temporary HDF5); `+ti64/setupMtex.m`, `readSettings.m`, `loadEbsd.m`, `exportEbsd.m` |
| Availability detection | `unavailable_reason()` (no engine start); Settings page shows unavailable backends with the reason |
| Sample sheet per map | `sample.py` (`SampleSheet`: specimen ID, input state raw/OIM-cleaned, build direction required as vector or angle from +x toward +y, 4PB geometry in mm and load in N, map position) |
| Loaders | .ang via both backends (hex grid, EDAX setting 2); `io/ang.py` (header, column check, PRIAS reader); `io/up2.py` (header, memory-mapped patterns); `io/bse.py` (JEOL image + .txt metadata) |
| GUI | `gui/app.py`: Project, Sample sheet, Settings pages |
| MCP | `mcp_server.py`: projects, settings, sample sheet, load, .up2 header, BSE, replay |

### Phase 2 – cleanup, maps, grains, boundaries
| Feature | Implementation |
| --- | --- |
| Cleanup (CI threshold, wild-spike removal, EBSD_part2 noise removal, grain-dilation fill, optional halfQuadraticFilter), preset from the raw / OIM-cleaned toggle, input never changed | `CleanupParams`, `CLEANUP_PRESETS` in `backends/base.py`; pymtex `PymtexBackend.cleanup` + `backends/hexgrid.py` (`neighbour_table`, `dilation_fill`); MATLAB `+ti64/cleanup.m`, `neighbourTable.m`, `dilationFill.m`; op `ebsd.cleanup` |
| Grain segmentation (HAGB 15°, LAGB 2° inside grains) | `GrainParams`; pymtex `calc_grains`/`get_grains` (`backends/pymtex_analysis.py`); MATLAB `+ti64/calcGrains.m`, `exportGrains.m`; op `grains.calc` |
| Boundary maps: LAGB, HAGB, α/β interface, Burgers α/α types (10.53, 60, 60.83, 63.26, 90°, tolerance 3°) | boundary table with misorientation and nearest Burgers type per segment (`burgers_types` from MTEX's Burgers variants); classes in `ops_ebsd.classify_boundaries` |
| KAM (order 1–3, cutoff), GROD, GOS, GND (basal/prismatic/pyramidal ⟨a⟩ + pyramidal ⟨c+a⟩, '1hop' stencil) | `MapParams`; pymtex `compute_map`, `gnd_map` (incl. pymtex 1.0.0 bug workaround, chunked); MATLAB `+ti64/computeMap.m`; op `map.compute` |
| Figures: IPF (key per phase and direction from Settings incl. build direction, phase filter), phase, CI, IQ, Fit, PRIAS, KAM, GROD, GOS, GND, boundaries, Burgers types, IPF key | `RenderSpec`; pymtex `backends/pymtex_plot.py` (native hex plotting); MATLAB `+ti64/render.m`, `hexRaster.m`, `ipfColors.m`, `renderKey.m` (corrected beta key); op `figure` |
| Comparison with OIM images | `modules/oim_compare.py` (map area detection, 8 flips/rotations scored, side-by-side PNG); op `compare.oim` |
| GUI / MCP | `gui/app.py` Maps page; MCP tools `cleanup_map`, `calc_grains`, `compute_map`, `make_figure`, `compare_with_oim` |
| Tests | `tests/unit/`, `tests/conformance/test_phase2.py` (both backends, 3 crops), `test_phase2_full.py` (full JU02), `matlab/tests/Phase2Test.m` |

### Phase 3 – prior-beta reconstruction
| Feature | Implementation |
| --- | --- |
| Reconstruction (EBSD_part2.m port: noise cleanup, alpha grains 1.5° + smoothing, Burgers OR + refinement with the visible 1° rule / orMode, variant graph or graph method, vote, merge single/progressive, inclusions, merge sweep with bridged steps / split noise, fit, run-log line) | `ReconParams` (`backends/base.py`); MATLAB `+ti64/reconstruct.m`; pymtex `backends/pymtex_recon.py`; backend method `reconstruct(handle, params, log_file)`; op `recon.run` (input = loaded map by default, D12) |
| Backend-neutral results | parent map = loaded map with parent orientations, props `parent_grain`, `recon_fit`, `recon_state` (`get_ebsd` of the reconstruction handle); `ReconData` (`get_recon`): parent grains (mean orientation, ECD, edge flag), parent boundaries (misorientation of the grain means), pre-merge beta/beta boundaries with neighbour misorientation; MATLAB `+ti64/exportRecon.m` |
| Ti variant and packet IDs (12 ideal Burgers variants, packet = {110}β ∥ (0001)α), variant-selection statistics (per map: share + mean fit per variant; per grain: number of variants / packets, largest variant share) | `modules/recon_analysis.py` (`ti_variants`, `grain_mean_parent`, `variant_stats`), Python-only, same for both backends (D13) |
| Prior-β grain size (ECD) and caliper length along / across the build direction, aspect ratio (edge grains excluded, minimum pixels and ECD) | `recon_analysis.grain_shape`; summary + per-grain table in `recon.run` outputs |
| Figures (drawn by the backend that computed the reconstruction): parent grain IPF map (grain mean, β key, boundaries > hagbAngle), α IPF with prior-β boundaries, fit, variant, packet, boundary misorientation map, β IPF key, histograms (neighbour misorientation, fit, ECD, variants per grain) | op `recon.figure`; `ipf_colors_quat` (pymtex / `+ti64/ipfColorsQuat.m`), `render_chart` + `ChartSpec` (matplotlib / `+ti64/renderChart.m`), per-segment line widths in `RenderSpec` |
| Parameter sweep (all combinations of a grid of ReconParams: reconstructed fraction, fit quantiles, grain counts, ECD, bridged steps, split noise, thumbnails) | op `recon.sweep` |
| GUI / MCP | `gui/app.py` Reconstruction page (`/recon`: parameters, summary + merge sweep, figures, sweep table + thumbnails); MCP tools `reconstruct_beta`, `recon_figure`, `recon_sweep` |
| Tests | `tests/unit/test_recon_analysis.py` (synthetic Burgers variants, calipers), `test_ops_recon.py` (JU02 crop1000: run, figures, sweep, MCP, replay), `tests/conformance/test_phase3.py` (both backends, 3 crop1000s, same OR), `test_phase3_full.py` (full JU02 vs reference), `matlab/tests/ReconTest.m` |
| Comparison script | `scripts/compare_recon.py` (full map, both backends, three OR modes), results in decisions D12 |

### Phase 4 – grain size (ISO 13067 / ASTM E2627 rules)
| Feature | Implementation |
| --- | --- |
| Rules: minimum pixels per grain (default 5), edge grains excluded, method reported with every number; QA warnings (pixels per grain, number of grains) | `GrainSizeParams` in `ops_grainsize.py`; decisions D14 |
| Alpha grain ECD, number- and area-weighted (lath partition = a `grains.calc` step, threshold configurable, default 15°) | op `grainsize.run`; MTEX area from each backend |
| Per-grain MTEX geometry: perimeter, longest / shortest caliper, caliper direction | backend method `grain_geometry` (pymtex `PymtexAnalysis.grain_geometry`; MATLAB `+ti64/grainGeometry.m`) |
| Lath thickness: minor axis of the second-moment ellipse; median linear intercept perpendicular to each lath's long axis | `modules/grain_size.py` (`moment_ellipse`, `GridIndex`, `chords`, `lath_intercepts`), Python-only, same for both backends |
| Colony / packet size: alpha grains merged across Burgers 10.53° boundaries (mean orientations, tolerance 3°) | `ColonyParams`; backend method `merge_colonies` (pymtex `grain2d.merge`; MATLAB `+ti64/mergeColonies.m`) |
| Prior-β ECD, width / length relative to the build direction: E112 mean intercepts across / along BD (edge chords counted half), chord distributions (line-length-weighted), per-grain calipers | `directional_intercepts`; `recon_analysis.grain_shape`; needs a `recon.run` step |
| Retained-β fraction (loaded map, analysed map, reconstruction) | `grainsize.run` summary |
| Statistics: mean, SD, median, p10/p90, number- and area-weighted log-normal fits (MLE, KS) | `dist_stats`, `lognormal_pdf`, `hist_edges` |
| Tables per map: summary, alpha grains, colonies, prior β (CSV) + one XLSX workbook | output kinds `csv` / `xlsx` in `service.py` (XLSX hashed by cell values, replay compares tables numerically) |
| Figures (drawn by the backend that computed the step): histograms with log-normal fits (alpha ECD number / area, lath thickness intercept / minor axis, colony ECD number / area, prior-β ECD, width, length), colony map | op `grainsize.figure`; `ChartSpec.curves` drawn by matplotlib and `+ti64/renderChart.m` |
| Validation against OIM grain size (OIM grain file export, same rules, KS test, overlay histogram) | `read_oim_grain_file`, op `grainsize.compare_oim` (format not yet verified on a real export) |
| GUI / MCP | `gui/app.py` Grain size page (`/grainsize`); MCP tools `grain_size`, `grain_size_figure`, `compare_oim_grain_size` |
| Tests | `tests/unit/test_grain_size.py` (synthetic rectangles / ellipses on square + hex grids, E112, log-normal), `test_ops_grainsize.py` (crop300 + crop1000 with reconstruction, tables, figures, OIM, MCP, replay), `test_gui_pages.py`, `tests/conformance/test_phase4.py` (3 crop300s + JU02 crop1000), `matlab/tests/GrainSizeTest.m` |

### Stubs (later phases)
`modules/registration.py`, `crack.py`, `bending.py`, `indexing.py`.

## TODO / open points

Data and inputs needed from the user:
- [ ] **OIM images** (IPF and CI exports of the test maps) in `data/reference/`, then run `compare.oim`
      (GUI Maps page or MCP `compare_with_oim`) for the IPF/CI comparison.
- [ ] **OIM figure with an in-plane reference** (IPF along RD/TD or an OIM pole figure) to settle the
      x↔y frame question (decision D1: setting 2 vs the bachelor-thesis convention).
- [ ] **JEOL BSE image + .txt pair** (JSM-IT500HR) to verify the BSE metadata parser (D5), plus images of
      both specimen edges for position fiducials.
- [ ] **Build direction of RI02 and RM02** (sample sheets); JU02 = 270° from the thesis.
- [ ] Which OIM cleanup steps were applied to the `_cleaned` maps; raw (uncleaned) exports if available.
- [x] Earlier results (`EBSD_data/JU`) copied to `data/reference/JU`.

Phase 3 open points:
- [ ] Caliper method for grain shape vs BD (pixel-centre extent along / across the in-plane BD + one step) is
      a first choice: confirm, or use e.g. the grain's principal axes / MTEX `caliper` (D13).
- [ ] Variant-selection statistics against the build direction (e.g. α c-axis angle to BD per variant) if needed.

Phase 4 open points:
- [ ] **OIM grain file export** of a test map (Analysis → Export → Grain File, with the grain tolerance and minimum
      size used) to validate the alpha ECD (`grainsize.compare_oim`) and to verify the parser (D14).
- [ ] Lath partition default: 15° makes colonies = alpha grains; 5° separates the 10.53° packet pairs (D14).
- [ ] ISO 13067 / ASTM E2627 texts (not in the literature folder) to confirm min_px and the QA thresholds.
- [ ] Prior-β boundaries are ragged at lath scale, so E112 intercepts (~22 µm on JU02) are far below the per-grain
      calipers; consider boundary smoothing of the parent grains before intercepts (D14).

Decisions to make:
- [ ] Keep retained beta in the noise cleanup? The EBSD_part2 rule removes ~1/3 of indexed beta (D10).
- [ ] PRIAS: re-export .ang with PRIAS columns, or virtual BSE from the .up2 later.

Technical:
- [ ] Remove the pymtex GND workaround once pymtex fixes `fitDislocationSystems` (D9).
- [ ] Cache loaded + cleaned maps per backend (CLAUDE.md; objects are currently rebuilt from the recipe).
- [ ] "Compare backends" tool (one step on both backends, numbers and figures side by side).
- [ ] MATLAB halfQuadraticFilter (`l1TVFilter`) is slow (~40× pymtex).
- [ ] Phases 5–10 (GUI/export, MCP/CLI, 4PB, crack, .up2 re-indexing, packaging).
