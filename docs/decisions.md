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
- Status: assumption, change it if you prefer um everywhere.

## D3 (2026-10-31) Machine-specific paths
- MTEX folder, MATLAB root and the literature folder are machine settings, read from `TI64_SETTINGS`,
  `settings.local.toml` (git-ignored) or `%APPDATA%\ti64app\settings.toml`, in that order
  (template: `settings.example.toml`). MATLAB uses the same lookup in `ti64.readSettings`; `ti64.setupMtex`
  adds MTEX to the path from it. Analysis settings stay in each project's recipe.
