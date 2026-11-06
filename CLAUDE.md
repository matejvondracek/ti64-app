# Ti64 EBSD/BSE microstructure analysis app

## Purpose
Local app for EBSD + BSE analysis of EBM Ti-6Al-4V (alpha + retained beta):
IPF/phase/CI/IQ/KAM maps, cleanup, grain boundaries, prior-beta reconstruction,
grain size, 4-point-bending stress mapping, fatigue-crack/microstructure analysis.
Used by a human through a GUI and by Claude through MCP tools. Same core functions for both.

## This repository is PUBLIC
- Never commit measurement data, literature, results, caches or credentials. .gitignore covers
  data/, literature, outputs and caches; run `git status` before every commit and stop if any of
  those appear.
- Test data live in data/test/ (git-ignored). Literature lives OUTSIDE the repo (see below).

## Architecture (do not change without asking)
- Two interchangeable analysis backends behind ONE interface, chosen by the user in Settings:
  - pymtex  (default): MTEX for Python (`mtex` on PyPI, pinned 1.0.0), runs in-process, no MATLAB.
  - matlab  (optional): MATLAB R2025b+ + MTEX 7.2 (pin the 7.x version) via the MATLAB Engine
                        (`matlabengine==25.2.*` for R2025b); code in matlab/+ti64/.
- python/ti64app/backends/base.py   AnalysisBackend protocol: every core operation, typed params
                                    (pydantic) and a backend-neutral result schema
- python/ti64app/backends/pymtex_backend.py, matlab_backend.py   the two implementations
- python/ti64app/   service layer: project + recipe model, job runner, settings,
                    Python-only modules (up2 indexing, BSE metadata, image stitching, registration, crack,
                    4PB mechanics)
- python/ti64app/gui/          NiceGUI front end (localhost), incl. Settings (backend, IPF key, figure style)
- python/ti64app/mcp_server.py MCP tools = thin wrappers over the service layer
- python/ti64app/cli.py        CLI for batch runs
- Each backend computes AND draws: MATLAB figures with MTEX plotting + exportgraphics, pymtex figures
  with its own (matplotlib) plotting. A figure always comes from the engine that computed it; the two
  may look slightly different and the user chooses the backend for their project/paper.
- Backends also return data (per-pixel arrays, grain/boundary tables, scalars) for the GUI, the
  Python-only modules and the conformance tests.
- Shared figure settings (IPF key per phase, reference direction, colour maps, line widths, scale bar,
  size, DPI) live in Settings and are passed to both renderers so the output is as close as possible.
- Overlays from Python-only modules (crack paths, positions, annotations) are passed as vector data to
  the active backend's renderer, so each exported figure comes from one renderer.
- Python-only modules consume backend results and never call a backend directly.
- Every GUI or MCP action is a recipe step in project/recipe.json, recording the backend name and
  versions (app, mtex/pymtex, MATLAB). Replay uses the recorded backend unless the user overrides it.
  Switching backend for a project marks its results stale.
- If MATLAB or the engine is not installed, the matlab backend is shown as unavailable, not an error.

## Backend contract and conformance
- Same parameter names, units and defaults in both backends (taken from docs/beta_reconstruction_method.md).
- tests/conformance/ runs every core operation on both backends on the JU02 crop and full map and
  compares results within stated tolerances (grain counts, ECD, fit statistics, KAM maps).
  Bit-identical results are NOT expected (e.g. OR refinement: MATLAB 0.82 deg vs pymtex 1.02 deg on JU02).
- A "compare backends" tool (GUI + MCP) runs one step on both and shows the numbers and both figures
  side by side. Conformance tests compare numbers, not images.
- New core features are added to both backends in the same change, or marked backend-specific in the
  interface with a clear "not available in <backend>" message.

## Legacy MATLAB (reuse the logic, not the structure)
- matlab_reference/ holds my earlier scripts: EBSD_part2.m (cleanup + prior-beta reconstruction +
  diagnostics), XRD_part2.m (XRD pole figures/ODF), stereograph_settings.m (stereographic plots of
  directional Young's modulus). Port them into matlab/+ti64 functions AND into the pymtex backend.
- docs/beta_reconstruction_method.md describes that method, its diagnostics and the JU02 reference results.
- docs/pymtex_recon_test.py is a working pymtex port of the reconstruction core (tested on JU02).
- Do NOT copy the old Python wrapper pattern (temp .m script, variables pushed into the workspace, clear all).
- hexRaster/hexRasterImage (fast hex-grid image) and the custom beta IPF key stay in the MATLAB
  backend. In pymtex use its native hex-grid plotting; add a raster shortcut only if it is too slow.
- EBSD_part2.m was written for MTEX 6.1. MTEX 7 changes that matter: default plotting convention is now
  y-down (set how2plot explicitly, as the script does), EBSD.load returns grid-ordered data (map to file
  rows via ebsd.oldId; check hexRaster's use of ids), calcGrains is tuned by 'angle', 'minPixel', 'alpha'.
  See the MTEX changelog ("Technical Changes").
- In MTEX 7.2 ebsd.how2plot is read-only, so `ebsd.how2plot.east = xvector` from EBSD_part2.m fails;
  set the convention through MTEX 7's plottingConvention API instead (check the 7.2 docs).

## IPF colour key (setting)
- Per phase, stored in the project recipe: key type ipfHKLKey (default, as in the Research Assignment),
  ipfTSLKey (OIM-like colours), ipfHSVKey (MTEX default), ipfSpotKey; reference direction X / Y / Z /
  build direction / custom vector; key drawn in the figure or exported as its own image.
- Same key names in both backends. The fixed beta key from EBSD_part2.m (no sliver along [001]-[101])
  is the MATLAB default for beta; check whether pymtex's HKL key shows the sliver.

## Data rules
- data/ is git-ignored and read-only for the app. Outputs go to projects/<name>/ (also git-ignored).
  - data/test/: 3 OIM-cleaned .ang maps (JU02, RI02, RM02; hex grid, 0.2 um step, ~2.6 M points each)
    and Ti64_DED_Area 2_OIM Map 2.up2 (43 GB, 6 M patterns of 60x60 px, square grid, 0.5 um step).
  - data/test/crops/: 300x300-pixel test crops, generated by a script from the full maps (never committed).
  - data/reference/: earlier results to reproduce (copy of EBSD_data/JU from my Research Assignment).
  - data/test/stitch/: JEOL SEM tiles for stitching tests: 8 positions x (BSE "BED-C" + SE "SED")
    .bmp + .txt in one folder; filter by $SM_DETECTOR / file name, never mix detectors.
- .ang files are TSL/OIM exports, possibly HEXAGONAL grid. Each map has an input-state toggle
  raw | OIM-cleaned (set by the user, auto-suggested from the file name); it selects the cleanup/CI/KAM
  preset, is part of the cache key, and changing it marks all downstream results stale.
  Load with EDAX setting 2 in both backends (MATLAB: EBSD.load(f,'setting',2); pymtex:
  loadEBSD(f, setting=2)) and use the same plotting convention in both (east = x, out of screen = -z).
  Verify the IPF map against an OIM screenshot before trusting any reference-frame-dependent result.
- The test .ang files declare PRIAS columns in the header but every data row has only 10 columns:
  there is no PRIAS data. Fit is constant 180 and SEM is 0, so only CI and IQ are usable. Use the IQ map
  as the reference image for BSE registration; virtual BSE exists only where patterns exist (.up2).
  If a future export does contain PRIAS columns, read them from the .ang text (pymtex 1.0 ignores them).
- .up2 = EDAX pattern file. Never load fully; stream in chunks (PyEBSDIndex or kikuchipy lazy=True).
  Neither MTEX version reads it.
- BSE images: JEOL JSM-IT500HR; metadata from the accompanying .txt (stage X/Y/Z/T/R, WD, kV, mag, pixel size).

## Literature (outside the repo)
- C:\Claude\literature\ holds the reference PDFs. Read them from there; never copy PDFs into the repo.
- Cite methods by DOI in code comments and docs/decisions.md.

## Environment and performance
- Always run Python, pip and pytest from the ti64 env: C:\Users\matej\miniforge3\envs\ti64\python.exe
  (e.g. `...\python.exe -m pytest`), never the system Python 3.10. Sessions started from the Claude
  desktop app do not activate conda.
- Windows, 16 GB RAM, conda env `ti64` with Python 3.12; mtex==1.0.0 pinned; MATLAB R2025b + MTEX 7.2
  optional. GPU optional (Intel Arc B580 via OpenCL), used only for .up2 indexing; everything else must
  run on CPU. Keep peak RAM < 8 GB.
- Cache each loaded + cleaned map per backend (pymtex: .npz/.h5; MATLAB: <name>_mtexcache.mat) under
  projects/ or a cache folder, never next to the raw data; keyed by file, input state and cleanup parameters.
- matlab backend: keep large objects in the MATLAB session; move per-pixel arrays to Python through
  temporary HDF5/.mat files (h5py), not element-wise engine conversion.
- Unit tests use the 300x300 crops (about 60 x 52 um, smaller than one prior-beta grain). Reconstruction
  tests use a larger crop (about 1000x1000 points, ~200 um) that contains whole prior-beta grains; the JU02 reference numbers are checked
  on the full map only, in slow integration tests.

## Science defaults (cite in code comments)
- Burgers OR beta->alpha: orientation.Burgers(csBeta, csAlpha), parent-to-child direction.
- Prior-beta reconstruction: follow docs/beta_reconstruction_method.md (my Research Assignment);
  background: MTEX parentGrainReconstructor, variant-graph method
  (Niessen 2022 doi:10.1107/S1600576721011560; Hielscher 2022 doi:10.1016/j.mtla.2022.101399).
  Threshold ranges from Yang 2025 (doi:10.1016/j.ijlmm.2025.07.009): reconstruction 5-8 deg, merging ~3 deg.
  The "use refined OR if <= 1 deg from Burgers" limit is a visible setting; report the deviation.
- Grain size: ISO 13067 / ASTM E2627 rules (min pixels per grain, exclude edge grains, report method).
- Build direction and 4PB geometry (outer/inner span, width, thickness, load) are entered by the
  user in the sample sheet, never guessed. Build direction drives the IPF reference
  direction default and beta-grain length/width relative to the build direction.
- Units: um, deg, MPa. Sample frame: X = beam axis, Y = thickness (neutral axis at Y=0), Z = width.

## Working rules
- Plan first, then implement; small commits per feature with tests (pytest; matlab.unittest for +ti64).
- Build each core feature in pymtex first, then in MATLAB, then pass the conformance test.
- After each feature, run it on the real maps in data/test/ and save a PNG for visual check (in projects/).
- When unsure about a physical/method choice, ask instead of guessing; list assumptions in docs/decisions.md.
