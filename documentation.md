# Ti64 EBSD/BSE analysis app – documentation

State after phase 6.6 and the fixes after the user test (2026-11-06). Details of decisions: `docs/decisions.md`; data facts:
`docs/data_audit.md`; timings and RAM: `docs/performance.md`; reconstruction method (phase 3):
`docs/beta_reconstruction_method.md`.

## Purpose

A local app for EBSD + BSE analysis of electron-beam-melted Ti-6Al-4V (alpha + retained beta):
orientation and quality maps, cleanup, grains and grain boundaries, prior-beta reconstruction, and later
grain-size statistics, 4-point-bending stress mapping and fatigue-crack analysis.

- One service layer, three front ends: a desktop app (NiceGUI in a pywebview window, for the user), MCP tools
  (for Claude) and a CLI.
- Two interchangeable analysis backends behind one interface, chosen in Settings:
  **pymtex** (MTEX for Python 1.0.0, default) and **MATLAB R2025b + MTEX 7.2** (optional).
  Each backend computes and draws its own figures; conformance tests check that both give the same numbers.
- Every action is a recipe step in `projects/<name>/recipe.json` (parameters, inputs, outputs with
  content hashes, backend, software versions), so any result can be replayed and checked.

## Running it

| What | Command (ti64 env: `C:\Users\matej\miniforge3\envs\ti64\python.exe`) |
| --- | --- |
| Desktop app | Start menu / Desktop shortcut **Ti64 EBSD-BSE**, or `Scripts\ti64-gui.exe` in the ti64 env (no console, no conda activate). One-time setup: `python -m pip install -e . --no-deps` (creates `ti64-gui.exe`), then `powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1`. Needs the Microsoft Edge WebView2 runtime (Windows 11 has it; else the Evergreen Bootstrapper from developer.microsoft.com/microsoft-edge/webview2). Log: `%APPDATA%\ti64app\logs\ti64-gui.log` |
| GUI for development / tests / Claude | `python -m ti64app.gui --browser [--port 8064] [--project <root or name>] [--no-show]` or `ti64 gui --browser` → http://127.0.0.1:8064; without `--browser` both open the desktop window |
| GUI click-through + screenshots | `python scripts/gui_screenshots.py [--no-stitch]` (Playwright + Chromium: `python -m playwright install chromium`; Windows dialogs mocked; screenshots in `projects/_gui_screenshots/`, demo project in `projects/_gui_demo/`) |
| Demo project (older scripts) | `python scripts/make_demo_project.py [--map crop1000\|full] [--no-recon]` |
| Viewer / export timings | `python scripts/bench_viewer.py --project phase5_full --map JU02_full` |
| MCP server | `ti64 mcp` (stdio). Register in Claude Code (machine-local, not committed): `claude mcp add ti64 --scope local -- C:\Users\matej\miniforge3\envs\ti64\Scripts\ti64.exe mcp`; check with `claude mcp get ti64`; new tools appear in the next Claude Code session |
| MCP demo (stdio client, both backends) | `python scripts/mcp_demo.py <map.ang> --angle <BD deg> [--map-id ID] [--prefix demo_mcp]` → projects `<prefix>_pymtex`, `<prefix>_matlab`, log `projects/<prefix>_log.json` |
| CLI | `ti64 backends`, `ti64 ops`, `ti64 run <recipe.json> [--project P] [--backend B] [--include-stale] [--until ID] [--no-compare] [--json]`, `ti64 replay <project>` |
| Stitch SEM images | `ti64 stitch <folder or files> [--scan] [--detector BSE\|SE\|both] [--se-layout bse] [--info-strip none\|keep\|generate] [--blend seam\|feather\|none] [--seam midline\|min_difference] [--band 32] [--no-stage] [--exclude 9771 ...] [--project P] [--json]`; GUI Stitch tab; MCP `scan_sem_images` (lists the stitch sets), `stitch_images` |
| Hexagons of both backends side by side | `python scripts/hex_backends.py` → `projects/_gui_screenshots/hex_backends.png` (+ `_detail.png`) |
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

### Phase 5 – GUI, map viewer, export
| Feature | Implementation |
| --- | --- |
| Project browser (all projects: backend, maps, steps, stale, last change; per map the pipeline status and Load / View) | `gui/pages.py` `project_page` (`/`) |
| Map viewer: zoom / pan (whole zoom levels), tiles for 2.6 M-point maps, scale bar, probe (point, phase, CI, IQ, quaternion, colour), distance / angle measurement | `gui/viewer.py` (`/viewer`), Vue component `gui/map_viewer.js` (NiceGUI's bundled Leaflet, flat CRS in µm, y down), tile routes `gui/tiles.py` (`/api/ti64/tiles`, `/api/ti64/overlay`, LRU cache) |
| Pixel geometry shared by both backends: brick cell layout of hex / square grids, nearest-neighbour sampling, tiles with averaging, line rasterisation, segment index, PNG / TIFF with physical pixel size | `raster.py` (decisions D15) |
| Layers coloured by the active backend: IPF (key + reference direction per phase), phase, CI, IQ, Fit, PRIAS, KAM, GROD, GOS, GND, prior-β IPF, reconstruction fit, variant / packet ID; colour key (backend IPF key image, colour bar, legend); cached on disk | `layers.py` (`LayerParams`, `get_layer`, `available`); backend method `measurement_colors` (pymtex `pymtex_plot.measurement_colors`; MATLAB `+ti64/valueColors.m`) |
| Overlays HAGB / LAGB / α/β interface / Burgers types / prior-β GB / crack, line width | `ops_ebsd.overlay_segments` (also used by the `figure` op: new overlays `beta_gb`, `crack`), `layers.get_overlay` |
| Annotations and ROIs (text, arrow, line, polyline, point, crack path, ROI rectangle / polygon) drawn on the map, recorded and undoable | `annotations.py`; op `annotations.set` |
| Crop to an ROI → new map (`.ang` rows inside the ROI, coordinates kept, sample sheet copied) | op `ebsd.crop` (`ops_export.crop_ang`) |
| Export: native (1 px / measurement), 2× / 4× / 8× nearest neighbour (PNG / TIFF, pixel size in tags + sidecar), or a figure exactly *w* mm wide at a DPI (PNG / TIFF / SVG / PDF, drawn by the backend), whole map / current view / ROI, overlays, annotations, scale bar, title, colour bar, legend, background; JSON sidecar with every parameter | op `export.map` (`ops_export.py`); `RenderSpec.exact / xlim / ylim / prims`; pymtex `pymtex_plot._save_exact`; MATLAB `+ti64/render.m` (`printExact`, `drawPrims`) |
| Recipe history: step table, parameters / inputs / outputs, re-run with edited parameters, undo / redo (also in the header), replay (all / up to a step / other backend / incl. stale) | `gui/pages.py` `recipe_page` (`/recipe`); `history.py`; `service.undo / redo / switch_backend`, `replay(until=)` |
| Parameter panels for every operation, generated from the op's pydantic model | `gui/pages.py` `ops_page` (`/ops`, `param_form`) |
| Settings: backend, IPF key / direction / custom vector / key-in-figure per phase, figure style (colour maps, line width, colour, scale bar, width, DPI), machine paths (override the TOML file) | `gui/pages.py` `settings_page`; `settings.save_preferences(paths=)` |
| MCP | `get_annotations`, `set_annotations`, `crop_map`, `export_map`, `undo`, `redo`, `recipe_history`, `viewer_layers` |
| Tests | `tests/unit/test_raster.py` (synthetic hex / square grids), `test_ops_export.py` (JU02 crop1000: layers, all export modes and formats, crop, undo / redo, replay, MCP), `test_gui_phase5.py` (simulated user: browser, viewer events, export, recipe undo / redo, op panels, settings), `tests/conformance/test_phase5.py` (colours, pixel-exact geometry, figure widths, SVG / PDF in both backends), `matlab/tests/ExportTest.m`, `tests/integration/test_gui_playwright.py` (slow, Playwright click-through) |

### Phase 6 – MCP tools, resources, CLI, compare backends
| Feature | Implementation |
| --- | --- |
| Every service operation has a dedicated MCP tool (`TOOL_FOR_OP`, test-checked) plus `run_operation` / `list_operations` for any op by name; 39 tools | `mcp_server.py` |
| Typed inputs: nested params are the service-layer pydantic models (`ReconParams`, `LayerParams`) or models derived from them without copying fields (`CleanupOverrides`, `GrainSizeOptions`, `ExportOptions`, `FigurePatch`, `AnnotationIn`); only the generic `params` of `run_operation` / `compare_backends` stay dicts (validated by the op's model) | `mcp_server._options`, `_partial`, `_set` |
| New tools: `compare_backends`, `run_recipe`, `run_operation`, `list_operations`, `get_step`, `verify_outputs`, `set_figure_settings`; tools refuse unknown projects instead of creating them (except `set_sample_sheet`, `run_recipe`) | `mcp_server.py` |
| Resources: `ti64://settings`, `ti64://backends`, `ti64://operations`, `ti64://projects`, `ti64://projects/{project}` (state + undo / redo), `.../recipe` (recipe.json), `.../steps/{step_id}`, `.../samples/{map_id}` | `mcp_server.py` |
| Stdio safety: protocol on private duplicates of fd 0 / 1; fd 1 and `sys.stdout` → stderr, fd 0 → NUL (pymtex prints the OR scan in cp1252, which corrupted the stream) | `mcp_server._private_wire` (decisions D16) |
| `ti64 run recipe.json`: runs a project's recipe.json or a hand-written list of `{op, params[, backend]}` steps into a (new) project, keeps step ids, compares recorded outputs; relative map paths resolved against the working directory and recorded absolute. Replay is the same function into a scratch project | `service.run_recipe`, `load_recipe_file`, `replay`; `cli.py`; GUI `/recipe` "Run recipe file"; MCP `run_recipe` |
| Compare backends: one backend op on pymtex AND MATLAB in a scratch copy of the project, every number of its JSON outputs side by side (abs / rel difference, > 1 % flagged), both figures (each from its own engine) + side-by-side montage; one `compare.backends` step in the project recipe | op `compare.backends` (`ops_compare.py`); GUI `/compare`; MCP `compare_backends` |
| Tests | `tests/unit/test_phase6.py` (tool coverage, typed schemas, protocol chain + resources via in-memory client, `ti64 run` of project and hand-written recipes, compare helpers), `test_mcp_stdio.py` (stray stdout over real stdio), `test_gui_phase6.py`, `tests/conformance/test_phase6.py` (compare cleanup via MCP, reconstruction on the three crop1000s) |

Demo (2026-11-01, full JU02 map, BD 270°, OIM-cleaned, default parameters, `scripts/mcp_demo.py` over stdio):
load → cleanup → `reconstruct_beta` → `recon_figure` → `export_map` (prior-β IPF + β GB, 120 mm, 300 dpi; native
IPF ∥ BD) on both backends, then `compare_backends` on `recon.run`. Results reproduce D12: pymtex OR dev 1.01° →
Burgers, 53 (796) grains, fit 3.57°, ECDaw 89.5 µm; MATLAB 0.97° → refined OR, 46 (708) grains, 3.36°, 94.4 µm;
the compare table flags `or_used` burgers vs refined as the cause. Times: pymtex recon 20 s, MATLAB 78 s, compare
109 s. `ti64 run projects/demo_mcp_pymtex/recipe.json` reproduced 21/21 recorded outputs (incl. the compare step).

### Phase 6.5 – SEM image stitching
| Feature | Implementation |
| --- | --- |
| Scan a folder / file list of JEOL images (.bmp first; .tif/.png/.jpg): image + .txt by base name, detector class from `$SM_DETECTOR` (file name as fallback), counts per detector, image number, stage, pixel size (`$CM_FIELD_OF_VIEW` / width), magnification, scan / stage rotation, beam shift | `modules/stitching.py` `scan`, `read_tile`, `detector_counts`; `io/bse.py` (real JEOL keys, D5 update) |
| Data bar cropped: file height − `$CM_IMAGE_SIZE` height (64 / 128 / 256 px at 1280 / 2560 / 5120); without .txt the 4:3 rule confirmed by strip detection; manual `strip_px` last | `stitching.detect_strip`, `read_tile` |
| Refuses tiles with another pixel size / magnification (unless `allow_rescale`), scan rotation, stage rotation or tilt | `check_consistency` |
| Stage → image axis mapping chosen from the data (signed permutations scored by overlap NCC); measured default +Y → down, +X → left | `choose_stage_map`, `DEFAULT_STAGE_MAP` (D17) |
| Pairwise registration: phase correlation + masked NCC surface at ~640 px, NCC score, subpixel phase correlation at full resolution; weak / far pairs rejected; all-pairs search without .txt; optional rotation check | `register_pair`, `ncc_surface`, `rotation_check` |
| Global least squares over good pairs anchored to the stage, worst pair dropped while residual > 3 px; stage-only tiles flagged | `solve_positions` |
| Per-tile gain / offset normalisation, linear feathering or no blending (raw pixels); integer placement, subpixel positions reported | `intensity_correction`, `render` |
| "Stitch BSE, apply the layout to SE": SE tile matched to its BSE tile (same image number), same canvas → aligned mosaics | `se_layout="bse"`, `_se_from_bse` |
| Info strip: none / keep original (single column; bottom tile's strip) / generate new (JEOL look, ranges, image numbers, redrawn round scale bar) | `modules/jeol_strip.py` |
| Output per detector: TIFF (BigTIFF + pyramid when large), PNG preview, JSON sidecar (inputs, positions px + µm, pixel size, stage → mosaic transform, scores, residuals, strip height), CSV tile table; band-wise rendering into a disk-backed array (RAM) | `write_tiff`, `write_preview`, op `sem.stitch` (`ops_stitch.py`, output kind `mosaic`) |
| GUI `/stitch`: folder / file browser, detector counts, options, stage-predicted thumbnail layout, stitch with progress, tile borders coloured by match score, click a tile → nudge / exclude → re-stitch, export dialog | `gui/stitch_page.py` |
| MCP `scan_sem_images` (read-only), `stitch_images`; CLI `ti64 stitch` | `mcp_server.py`, `cli.py` |
| Tests | `tests/unit/test_stitching.py` (synthetic tiles with known subpixel offsets, noise, gain changes, fake data bar, three stage mappings, no-.txt path, blending, SE layout, strips, pyramid, recipe replay, MCP + CLI), `test_gui_stitch.py`, `tests/integration/test_stitching_real.py` (data/test/stitch + JU04–JU06) |

Real data (data/test/stitch, BSE, 2026-11-06): 8 tiles → 2618 × 11 861 px at 10 nm/px, all 7 neighbour pairs
registered (NCC 0.44–0.88), no stage-only tiles; order top → bottom 9776, 9775, 9774, 9773, 9772, 9771, 9777,
9778 (= increasing stage Y). BSE + SE with the BSE layout and kept strip: 20 s, peak RAM 0.9 GB.

### Phase 6.6 – GUI redesign: desktop window, Files / EBSD / Crack / Stitch tabs, samples, autosave
Layout from the sketches in `docs/gui_mockups/` (01_home, 02_ebsd, 03_files); one colour scheme (navy top bar, blue
primary, white cards; `gui/theme.py`), rounded buttons, number boxes with increment arrows, hover explanations for every
option (`gui/tips.py`). Decisions: D18 (samples / files), D19 (desktop, autosave, recovery). The phase 5 / 6 pages
(`/viewer`, `/maps`, `/recon`, `/grainsize`, `/ops`, `/recipe`, `/compare`, `/sample`, `/settings`) now live in these tabs;
the old URLs redirect.

| Feature | Where |
| --- | --- |
| Desktop window "Ti64 EBSD/BSE" (pywebview + Edge WebView2, server thread on a free port), `ti64-gui.exe` gui-script, app icon `docs/icon.ico`, Desktop / Start-menu shortcut, log file + plain error box with "Open log", WebView2 check, one window per project (`.window.lock`; opening it again brings the window to the front, another project opens a new window) | `gui/desktop.py`, `scripts/make_shortcut.ps1`, `pyproject.toml` `[project.gui-scripts]` |
| `--browser` mode for Playwright / Claude / development; Windows dialogs native in the window, tkinter in the browser, mocked in tests | `gui/app.py`, `gui/dialogs.py` |
| Home (no project): New Project (name + location), Open Project (folder dialog + recent projects), title page | `gui/home.py`, `gui/shell.py` |
| Top bar: tabs Files / EBSD / Crack / Stitch, project name with menu (new, open, save copy as, show folder, close), "Saved / Saving... / error" indicator, Settings gear → dialog (backend + availability, IPF key per phase, figure style, paths, project disk use + Clear cache), every change saved at once | `gui/shell.py` |
| Files tab: Add file (multi-file dialog, linked by absolute path), Remove (project only, confirmed), tree Project → Sample → files grouped by type and folder, JEOL .txt hidden, missing files greyed with Relocate, changed files flagged, New sample, drag and drop / right-click "Move to sample", Auto-assign (suggested from the file / folder name, confirmed in a list), sample sheet and map sheet (inherited values greyed "from sample", per-file overrides), file metadata (.ang header, JEOL .txt, .up2 header), open a file in its tool (double-click, Enter or button) | `gui/files_tab.py`; `sample.py` `SampleInfo`, `MapInfo`; `project.py` |
| EBSD tab: accordion of edit sections (Load and cleanup, Maps and IPF key, Grains and boundaries, KAM / GND, Prior-β reconstruction + figures + sweep, Grain size + figures + OIM, Annotations and ROIs, OIM image, any operation), History (undo / redo, re-run with edited parameters, replay, run a recipe file, restore a recipe version, interrupted step) and Compare backends; Apply = one recipe step with a progress bar; fields start from the last applied step; unapplied values kept as drafts ("● not applied"); Export and Compare to original (hold: mouse or Space / Enter) at the bottom; right: Sample / file dropdown, map type, overlays, tool, the tiled viewer and a Results tab | `gui/ebsd_tab.py`, `gui/history_panel.py`, `gui/viewer.py` + `map_viewer.js` (`showAlt`, `initial_view`) |
| Export dialog (EBSD and Stitch): PNG / TIFF / SVG / PDF, native / 2× / 4× / 8× / width in mm at DPI, region, scale bar, IPF key / colour bar, overlays, annotations, info strip, Windows save dialog, last choices remembered; mosaics through the new op `sem.export` | `gui/export_dialog.py`, `ops_stitch.py` |
| Stitch tab: images = all images of a sample / a folder / picked files, detector filter with counts, info strip, blending, min. score; layout, result (tile borders by score, nudge / exclude, re-stitch), image view with metadata | `gui/stitch_page.py` |
| Crack tab: placeholder until phase 8 | `gui/app.py` |
| Autosave and recovery: atomic writes of every state file, `gui_state.json` (tab, selection, viewer bounds, overlays, drafts, export choices), undo history in `.history/undo/` (survives closing and Clear cache), last 20 recipe.json versions in `.history/recipe/`, `running.json` → "interrupted, rerun?", Save copy as, linked-file size + mtime | `project.py`, `recipe.py`, `history.py`, `gui/state.py` |
| Samples in the recipe: every step records `sample_id`; output folders `outputs/<sample>_<step>/`; export names start with the sample ID; the current map sheet is recorded as a `sample.set` step before an op uses the map (so the recipe alone replays the project); a sheet change marks results stale only when input state, build direction, 4PB, position or the file change | `service.py` (`record_sheet`, `step_sample`) |
| Tests | `tests/unit/test_project_samples.py` (inheritance, migration, stale rules, recorded sheets + replay, files, recipe versions, interrupted, copy, cache), `test_gui_layout.py` (home → new project → files / samples / auto-assign → EBSD load + cleanup + drafts + undo → Settings → interrupted → Crack), `test_gui_pages.py`, `test_gui_phase5.py`, `test_gui_phase6.py`, `test_gui_stitch.py` (sections of the new tabs), `tests/integration/test_gui_playwright.py` (slow: `scripts/gui_screenshots.py`, incl. holding Compare to original and both exports) |

Screenshots (`projects/_gui_screenshots/`, made by `scripts/gui_screenshots.py`; git-ignored): home and Open menu,
new-project dialog, Files tab (empty, unassigned, by sample, auto-assign, sample sheet, map sheet, image sheet, context
menu), EBSD tab (not loaded, loaded, draft, running, applied, Compare to original held by mouse / keyboard, each
section, tooltip, History, Compare backends), export dialog, Settings, Stitch (layout, result, export), Crack, keyboard
focus, and Files / EBSD / Stitch at 1920 × 1080; `00_desktop_window.png` is the native window.

### Phase 6.6 fixes after the user test (2026-11-06)
Decisions D20 (true hexagons), D21 (stitching), D22 (GUI). Tabs are now **Files | View | EBSD | Crack | Stitch**.

| Change | Where |
| --- | --- |
| Stitching never mixes magnifications / pixel sizes: a selection is grouped into **stitch sets** (same magnification + resolution + overlapping stage footprints, e.g. JU04 9475–9479 at 10000×); the Stitch tab lists the sets, single images are listed as not stitchable; a mixed selection is refused with the list of sets (`allow_rescale` removed) | `modules/stitching.py` (`stitch_sets`, `check_one_set`), `gui/stitch_page.py` |
| **Arrange view**: thumbnails at the stage positions (in a row without stage data), drag, zoom, lock; select with click / Shift-click / Shift-drag box (= the images to stitch, also a checklist filtered by detector); **Refine** searches only within a radius (default 10 % of the tile size) around these positions; the manual layout is saved in the recipe (`manual_layout`, `locked`, `refine_radius`) | `gui/arrange_view.js`, `gui/stitch_page.py`, `modules/stitching.py` |
| **No ghosting**: subpixel placement (cubic resampling of the fractional offset), full-resolution subpixel pair refinement, blending only in a band around each seam (`blend="seam"`, 32 px; seam = midline or a minimum-difference path); residual misalignment after placement per overlap + a difference-image view (Overlaps tab) | `modules/stitching.py` (`render`, `seam_owner`, `min_difference_seams`, `pair_alignment`, `overlap_difference`) |
| Info strip "keep the original data bar": greyed out with the reason as a hover tooltip when the images are not one column | `gui/stitch_page.py` |
| **View tab**: any image or mosaic (zoom / pan, metadata, draggable scale bar, export), `.up2` files (header table + pattern viewer: row / column, step to neighbours; indexing in phase 9) | `gui/view_tab.py`, `gui/tiles.py` (`ImageLayer`, thumbnails), `io/up2.py` (`read_pattern`, `pattern_index`) |
| Files tab: **Find missing…** searches a folder (with subfolders) by name and size and relinks all missing files at once (list of what was found); the list updates immediately (stale-slot bug fixed for all dialogs: `state.stable`, row clicks ignore buttons); Add file starts with "All files (*.*)"; right-click "New sample for this file" asks for the ID (prefilled) and moves the file; clicking a sample's name folds its files (remembered) | `gui/files_tab.py`, `gui/dialogs.py`, `project.py` (`find_missing`) |
| EBSD tab: overlay menu in columns (grows to the right) with colour and line width per boundary type; map tabs **α map · prior β · Results**, each map tab with its own layer, overlays and history; the reconstruction focuses the prior-β tab (its prior-β boundaries shown, D24) | `gui/ebsd_tab.py`, `ops_ebsd.py` (`OverlayStyle`), `layers.py` |
| **History panel right of the map**: drag a step onto another to reorder; invalid orders are refused with the reason (e.g. "Cleanup needs a loaded map before it"); a valid order re-runs from the first changed step, old results stay as stale, one undo entry | `gui/history_panel.py`, `reorder.py`, `history.py` (`group`) |
| **True hexagons**: the viewer draws regular hexagons when a cell is ≥ 8 screen px (zoom indicator says brick / hexagons, tooltip explains both); exports: native and 2× brick, 4× / 8× hexagons drawn by each backend (`cell_image`: pymtex `raster.hexagon_image`, MATLAB `+ti64/hexRasterize.m`); MATLAB `render.m` figures with ≥ 4 px per measurement use hexagons | `raster.py`, `backends/*`, `matlab/+ti64/hexImage.m`, `hexRasterize.m`, `render.m` |
| **Export dialog**: final size in px, physical size in µm, estimated file size; type the width or height in px (aspect locked), or scale / mm at DPI; hexagon note for hex maps; no legend options any more | `gui/export_dialog.py` |
| **Legends on the map** (scale bar, colour bar / colour legend, IPF key): toggled in the map toolbar, dragged and resized on the map; exports put them exactly there (PNG / TIFF burnt in, SVG embedded, PDF overlay page via pypdf) | `gui/map_viewer.js`, `legends.py`, `ops_export.py`, `ops_stitch.py` (`sem.export`: also single images) |
| Tests | `tests/unit/test_stitching.py` (stitch sets, subpixel no-ghosting, min-difference seam + difference image, manual layout + lock + radius), `test_hexagons.py` (per backend, MATLAB marked), `test_reorder.py`, `test_gui_layout.py` (find missing, new sample for a file, fold), GUI tests updated; `scripts/gui_screenshots.py` covers every changed screen |

Screenshots of every changed screen (`python scripts/gui_screenshots.py`, 46 screens, plus
`python scripts/hex_backends.py` for the pymtex / MATLAB hexagon comparison) are in `projects/_gui_screenshots/`
(git-ignored; `index.html` shows them all).

### Phase 6.6 fixes, second round (2026-11-09)
Decisions D23–D28.

| Change | Where |
| --- | --- |
| A map **loads without sample or build direction** (input state suggested from the file name until set); steps that need the build direction say so with an **Open map sheet** button (Files tab, file selected); steps that can do the rest run and say what they left out; entering the BD later only makes BD-dependent results stale | `sample.py` (`SheetIncomplete`, `require_build_direction`, `stale_on_sheet_change`), `project.py`, `ops_*`, `gui/shell.py` (`sheet_missing`), `gui/files_tab.py` (`/files?sel=`), MCP `set_sample_sheet` |
| The selected map **loads by itself** when the EBSD tab opens or another map is picked (progress shown); loaded-map **cache per backend** in `cache/maps/` | `gui/ebsd_tab.py`, `service.py` (`ebsd.load`) |
| **Replay**: profiled (`scripts/profile_replay.py`), unused backend objects released (RAM 6.5 GB → ≤ 0.6 GB, no MemoryError), no value-by-value comparison with another backend's outputs; progress dialog "step 3 of 9: … (pymtex)", bar, elapsed time, Cancel; slowest steps listed | `service.py` (`run_recipe`, `replay`, `release_stale`), `gui/history_panel.py` (`run_with_progress`) |
| History: **Delete all steps…** / **Delete stale steps (n)…** (map tab or whole project, confirmation, dependents included, one undo entry) | `service.py` (`plan_delete`, `delete_steps`), `recipe.py` (`references`), `gui/history_panel.py` |
| **MATLAB engine**: own headless process (`-nodesktop -nosplash`, never the user's MATLAB), crash watchdog, one restart with a plain-language message, the step runs again; logs in `%APPDATA%\ti64app\logs\` | `backends/matlab_backend.py`, `backends/base.py` (`EngineRestarted`), `logs.py`, `service.py` |
| Edit menu: **switched-off options hide their parameters**; only maps / overlays / figures that exist are offered (no Fit / PRIAS when not stored, α boundaries after grains, prior-β boundaries after the reconstruction, crack after drawing one) | `gui/ebsd_tab.py`, `layers.py` (`available`, `stored_data`) |
| **Prior β tab** shows the prior-β grain boundaries of the parent grains; **grain size works on the map tab it is started from** (`grainsize.run target`); histogram **bin width** (µm) with the log-normal fits | `ops_grainsize.py`, `modules/grain_size.py` (`hist_edges`), `gui/ebsd_tab.py`, `gui/history_panel.py`, `reorder.py` |
| Boundary lines keep their **screen width** while zooming; **legends anchored to the map** (move / scale with zoom and pan, exported exactly there for any region) | `gui/map_viewer.js`, `legends.py` (`frame`, `in_region`), `ops_export.py` |
| **.up2 overview**: pattern-quality / virtual-BSE map computed once by streaming (subsampled, cached, cancellable, resumable); a click loads only that pattern (row, column, x, y) | `modules/up2_overview.py`, `gui/view_tab.py`, MCP `up2_overview` |
| **Stitch**: one Stitch button (refinement within the search radius); unknown stage axes (no spread / identical positions) searched by image matching and reported, possible repeats flagged (JU04); export size estimate with 1/2–1/8 and tiled BigTIFF, large-export failure fixed; one scale bar with the info strip | `modules/stitching.py`, `ops_stitch.py`, `gui/stitch_page.py`, `gui/export_dialog.py`, `hashing.py` |
| Tests | `tests/unit/test_fix2_ebsd.py`, `test_matlab_engine.py`, `test_up2_overview.py`, `test_stitching.py`, `test_gui_stitch.py`; older tests updated to the new rules |

Screenshots of the changed screens: `python scripts/gui_screenshots_fix2.py` → `projects/_gui_screenshots_fix2/`
(git-ignored; `index.html`).

### Phase 6.6 fix 3 – View and Stitch (third user test, decisions D30)

| Change | Where |
| --- | --- |
| **View**: .bmp / .tif images open with the **microscope info bar** ON (JEOL data bar: detector, kV, WD, magnification, scale bar, image number, date) and the app's scale bar OFF; an image without an info bar shows the app's scale bar; the choice is remembered per image; the export starts from what the view shows | `gui/view_tab.py` |
| **Stitch result zoomable**: the mosaic opens in the tiled viewer (pan / zoom to full resolution; pyramid TIFF read lazily, a few tens of MB), optional **tile borders** (coloured by match score, image number · score) and **seams** (saved by `sem.stitch` as output `seams_<DET>`; older steps: midline seams computed for display); click a tile to nudge / exclude it | `gui/mosaic_tiles.py`, `gui/stitch_page.py`, `modules/stitching.py` (`seam_segments`) |
| **No stage-axis warning / no automatic full-range search** along a constant stage axis (JU04 was a faulty stage); new option **Do not use stage positions** (`sem.stitch use_stage=False`; Stitch tab under 'Stitch sets', MCP `stitch_images use_stage`, CLI `--no-stage`): placement only from image matching of all pairs (maximum spanning tree of the strongest matches, then least squares) or the Arrange positions; sets are not split by stage position; the Arrange view starts from a grid | `modules/stitching.py` (`_register_by_matching`, `_consistent_with_tree`, `stitch_sets`, `layout_preview`), `gui/stitch_page.py`, `mcp_server.py`, `cli.py` |
| Tests | `tests/unit/test_stitching.py` (no unknown axis, matching without stage, repeats, seam lines), `test_gui_stitch.py` (option + zoomable result, lazy pyramid reader, View defaults) |

Screenshots: `python scripts/gui_screenshots_fix3_stitch.py` → `projects/_screens_fix3/` (git-ignored).

### Phase 6.6 fix 3 – top bar, export log, EBSD tab (third user test, decisions D29, D31)

| Change | Where |
| --- | --- |
| **Memory** in the top bar: app (Python + MATLAB engine) and PC used / total, every 3 s, tooltip, orange above 80 % | `sysinfo.py`, `gui/shell.py` (`right_tools`) |
| **Feedback** button: Bug / Idea / Question, description, version, backend, OS, last 100 log lines (paths shortened to file names), optional window screenshot (off by default); "Open GitHub issue" (pre-filled) or "Send e-mail" (subject "[Ti64] Bug" / "Idea" / "Question", report .zip selected in Explorer); repo URL and address in Settings > Feedback | `feedback.py`, `gui/shell.py`, `settings.FeedbackSettings` |
| **Exports are not recipe steps**: `export_log.json` (settings, file, date), files in `exports/<id>/`, "Earlier exports → Export again" in the Export dialog, MCP `export_log` / `export_again`; old projects migrated on open | `exportlog.py`, `service.export / export_again`, `gui/export_dialog.py` |
| Export: default folder = Pictures, then the last folder per project; Include boxes = what the view shows; "Add the microscope info bar under the image (…)" with tooltip | `settings.pictures_dir`, `gui/export_dialog.py` |
| **EBSD load**: progress bar with the stage names in the map area; JU02 click → first image 1.8–4.6 s (pymtex), see D29 | `progress.py`, `gui/ebsd_tab.py` |
| **KAM / GROD / GOS / GND** in the map list, computed on first use; section 'KAM / GROD / GOS / GND' holds only the parameters | `gui/ebsd_tab.py` |
| **Prior-β grains**: 'Grains and boundaries' on the prior β tab segments the reconstructed parent map (both backends); stays on the tab, HAGB overlay on there | `ops_ebsd.resolve_grains`, `layers.newest_grains`, `matlab/+ti64/calcGrains.m` |
| **Legends**: IPF key only on IPF maps (colour bar instead on scalar maps, no placeholder text); snap to corners / edge centres with a margin, while dragging or in 'Legend layout'; auto size | `gui/map_viewer.js`, `legends.LegendItem`, `gui/ebsd_tab.py` |
| **History** right-click → Delete (dependents: delete too / mark stale; undoable) | `gui/history_panel.py`, `service.delete_step` |
| **Results** right-click → Delete / Save (MATLAB .fig, .svg / .pdf vector) / Export (png, jpg, tif, svg, pdf) | `service.save_figure / export_result`, `matlab/+ti64/+plot/saveFigure.m` |
| Grain-size histogram: shown range min / max (µm) besides the bin width | `ops_grainsize`, `modules/grain_size.hist_edges` |
| Tests | `tests/unit/test_fix3.py`, `test_fix3_gui.py`, `test_fix3_load.py`, `tests/conformance/test_fix3.py` (prior-β grains + Save formats in both backends) |

Screenshots: `python scripts/gui_screenshots_fix3.py` → `projects/_screens_fix3/main/` (full JU02 map; git-ignored).

### Phase 6.6 fix 4 – APEX phases, PRIAS, hex .up2, reports for Claude (fourth user test, decision D32)

| Change | Where |
| --- | --- |
| Phases identified by **crystal symmetry** (6/mmm → α, m-3m → β) and renamed 'Ti (alpha)' / 'Ti (beta)' at load in both backends; original names in the load meta; map sheet section 'Phases' (role per phase, needed only when ambiguous) | `phases.py`, `service._ebsd_load`, `backends/pymtex_backend.load_ebsd`, `matlab/+ti64/loadEbsd.m`, `gui/files_tab._phases_section` |
| MATLAB: single-phase .ang no longer comes back as "not indexed" (phaseMap = CSList order) | `matlab/+ti64/loadEbsd.m` |
| Cleanup fill: large not-indexed regions without a grain are no longer filled as 1-px voids (both backends) | `backends/hexgrid.component_sizes`, `pymtex_backend.cleanup`, `matlab/+ti64/cleanup.m` |
| **PRIAS text exports** next to the .ang: detected (also on Add file), channel from the header, matched by x / y within half a step, clear mismatch messages; 'PRIAS top / centre / bottom strip' in the Maps list; registration reference PRIAS else IQ | `io/prias.py`, `ops_ebsd.figure_spec`, `layers.stored_data`, `gui/files_tab._prias_section`, `modules/registration.py` |
| **Hex .up2** with the extra-patterns flag: padding pattern at the end of each short row skipped (no diagonal seam); point-order access for PyEBSDIndex / kikuchipy | `io/up2.py`, `modules/up2_overview.py` |
| **.up2 ↔ .ang by scan ID**; View tab: paired map, its IQ / CI / PRIAS on the same cells, correlation + best shift | `io/up2.find_paired_ang`, `modules/up2_pair.py`, `gui/view_tab.show_up2`, MCP `read_up2_header` |
| .up2 overview drawn as **hexagonal cells** (EBSD viewer renderer) | `modules/up2_overview.cells / layer`, `gui/tiles.register_layer` |
| Feedback without e-mail; **Save report for Claude** (Help menu, Feedback dialog, Ctrl+Shift+R) → `reports/<date>_<category>.md` + `.json` (+ `.png`) | `report.py`, `breadcrumbs.py`, `gui/shell.py` |
| Stitch tab: one Export button | `gui/stitch_page.py` |
| Tests | `tests/unit/test_fix4.py`, `tests/integration/test_fix4_real.py` (JU04 / JU06, both backends) |

Screenshots: `python scripts/gui_screenshots_fix4.py` → `projects/_screens_fix4/` (git-ignored).

### Stubs (later phases)
`modules/registration.py`, `crack.py`, `bending.py`, `indexing.py`.

## TODO / open points

Data and inputs needed from the user:
- [ ] **OIM images** (IPF and CI exports of the test maps) in `data/reference/`, then run `compare.oim`
      (GUI Maps page or MCP `compare_with_oim`) for the IPF/CI comparison.
- [ ] **OIM figure with an in-plane reference** (IPF along RD/TD or an OIM pole figure) to settle the
      x↔y frame question (decision D1: setting 2 vs the bachelor-thesis convention).
- [x] **JEOL BSE image + .txt pairs** (JSM-IT500HR): parser verified on 197 real pairs (D5 update, D17).
- [ ] Images of both specimen edges for position fiducials (phase 7).
- [ ] A real **2D grid** of overlapping SEM tiles (only 1D sets in data/) to test stitching with loops in the pair
      graph; and tiles at another stage rotation to check that the stage → image mapping (D17) does not depend on R.
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
- [x] PRIAS: separate OIM text exports <map>_prias.txt (fix 4, D32).
- [ ] Raw preset CI 0.1 on the fine-step raw APEX maps JU04_area1/2 (78 / 63 % of points removed, D32).

Technical:
- [ ] Remove the pymtex GND workaround once pymtex fixes `fitDislocationSystems` (D9).
- [ ] Cache loaded + cleaned maps per backend (CLAUDE.md; objects are currently rebuilt from the recipe).
- [ ] MATLAB halfQuadraticFilter (`l1TVFilter`) is slow (~40× pymtex).
- [ ] Phases 7–10 (4PB, crack, .up2 re-indexing, packaging).
- [ ] Renderer scale bars differ (pymtex 25 µm vs MATLAB 50 µm on the same full-map recon figure); harmonise the scale-bar length rule.

Phase 6.6 fixes, open points:
- [ ] Reordering re-runs the map's steps from the first changed one; on full maps that can take minutes (progress in
      a notification). Steps of other maps are never moved.
- [ ] The stage → image mapping of the Arrange view's starting positions is the measured JSM-IT500HR default (D17);
      Stitch (not Refine) still chooses the mapping from the data.
- [ ] Legends in SVG exports are embedded raster images (the map and boundaries stay vector).

Phase 6.6 open points:
- [ ] Specimen frame ↔ JEOL stage: the map position is entered by hand; converting stage X/Y to the specimen frame
      needs a reference (edge fiducials, phase 7).
- [ ] Role suggestion needs the tension side (+Y / -Y) on the sample sheet; confirm the convention for your rig.
- [ ] Drag and drop in the file tree moves one file at a time (several: right-click per file or Auto-assign).

Phase 5 open points:
- [ ] Pixel-exact exports: colours come from the backend, the pixel layout and line rasterisation from shared Python
      code (D15). Confirm, or port the layout to +ti64 so MATLAB draws them end to end.
- [ ] Native export of a hex map has non-square pixels (0.2 × 0.1732 µm, declared in the file) and the shifted
      rows half a pixel to the left (D15). Confirm this definition of "1 px per measurement".
- [ ] Probe shows the quaternion, not Euler angles (convention to be checked against OIM before showing Euler).
- [ ] Viewer: per-phase IPF keys of the view are passed to exports; the backend IPF key image shows one phase
      at a time.
