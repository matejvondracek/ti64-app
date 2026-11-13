# Orientation check: app vs OIM Analysis (JU04_area4)

Phase 6.6 fix 5, items 1–5. Date: 2026-11-13. Backends: pymtex (mtex 1.0.0) and MATLAB R2025b + MTEX 7.2.

## What was compared

- **Map:** `data/JU04/JU04_area4.ang`: raw APEX export, hexagonal grid, 1501/1500 × 1733 points, 0.2 µm step,
  one phase "Ti (Titanium, alpha)". The header says `CartesianAlignment a || x, c* || z`.
- **OIM reference:** two photos of the OIM Analysis screen, in `data/reference/JU04/` (git-ignored):
  - `JU04_area4_OIM_IPF.jpg`: "Auto 001 IPF", *Color Coded Map Type: Sample Direction [0 0 1]*. The legend shows
    A1 up and A2 left. The status bar shows the probe point.
  - `OIM_coordinate_settings.jpg`: Preferences › Coordinate Settings, **Setting 2** selected. The map's x points
    right and y down.
  - These are camera photos of a monitor, not screen captures. The colours are washed out (saturation 0.10–0.15
    over the whole map), and the map is seen at a slight perspective.
- **Script:** `scripts/orientation_check.py [pymtex|matlab]`. Outputs go to `projects/orientation_check/`
  (`JU04_area4_oim_vs_app_<backend>.png`, `result_<backend>.json`).
- **Tests:**
  - `tests/integration/test_fix5_orientation.py`: probe point, crystal frame and IPF axis mapping in both backends,
    plus every .ang in data/ in both backends.
  - `tests/unit/test_fix5.py`: frame maths.

## 1 Probe point (row 86, column 305, 0-based)

| | OIM status bar | .ang line | app (both backends) |
|---|---|---|---|
| x, y (µm) | 61.00, 14.90 | 61.00000, 14.89564 | 61.0, 14.8956 |
| Euler φ1 Φ φ2 (°) | 147.4, 34.1, 98.4 | 2.57265 0.59602 1.71747 rad = 147.402, 34.149, 98.404 | file: 147.4, 34.1, 98.4 · app frame (setting 2): 302.6, 145.9, 278.4 |
| IQ / CI | 78013.4 / 0.436 | 78013.4 / 0.436 | 78013.4 / 0.436 |
| crystal direction ∥ A3 | [5 −2 −3 7] | – | [5 −3.14 −1.86 7] ≡ [5 −1.86 −3.14 7] (6/mmm) |

- The probe (EBSD tab, tool "probe") now shows the file Euler angles in degrees, as OIM does. It also shows the
  app-frame Euler angles, both labelled, plus scan row and column (`ti64app/probe.py`).
- Both backends hold exactly the file orientation rotated by the setting-2 rotation (difference < 0.001°).

## 2 Map: OIM photo vs app render

The photo's map area was rectified with 4 corners, refined by an ECC homography. The app render is the
alpha phase in **ipfTSLKey along app Z**. The OIM map is "Sample Direction [0 0 1]" = A3, and app z = −A3,
which gives the same colours because the IPF key uses the Laue group.

Colours were compared after an affine colour fit of the photo (white balance and contrast of the photographed
monitor). The pattern was compared by Pearson r (mean of the three channels).

| | pymtex | MATLAB |
|---|---|---|
| median RGB distance, photo colour-corrected (0…√3) | 0.284 | 0.284 |
| median RGB distance, uncorrected | 0.373 | 0.373 |
| Pearson r, identity | **0.632** | **0.632** |
| r, mirror x / mirror y / rot 180 | 0.06 / 0.00 / −0.03 | same |
| r, transpose / rot 90 / rot 270 / anti-transpose | 0.02 / 0.01 / 0.01 / 0.00 | same |
| r, crystal frame rotated 30° about c (X‖a*) | 0.247 | 0.247 |
| r, load settings 0 / 1 / 3 / 4 | 0.632 (all) | same |

- **The map is not mirrored or rotated.** The identity is the only transform with a correlated pattern. OIM's
  origin is top left with the first row on top, and so is the app's.
- The colour distance is dominated by the photo: 0.28 even after the fit, with no dependence on CI. So the pattern
  correlation decides, not the median colour. A screen capture (Alt+PrtScn) would allow a true colour comparison.
- **IPF along z cannot tell the load settings apart.** Every EDAX setting is a 180° rotation about z or about an
  in-plane axis, so z maps to ±z and the colours are identical. The settings are told apart by the probe point
  (section 1) and by IPF-X / IPF-Y (section 4).
- **Crystal frame:** X‖a* drops r from 0.63 to 0.25, so X‖a is right (section 3).
- Figure: `projects/orientation_check/JU04_area4_oim_vs_app_<backend>.png`, with four panels: OIM rectified ·
  OIM colour-corrected · app · |difference|.

## 3 Crystal frame

- The .ang header says `a || x, c* || z`.
- Both backends load hexagonal phases with **X‖a, Y‖b\*, Z‖c**: the EDAX convention, as reported by pymtex's
  `crystalFrame.alignment()` and MTEX 7.2's `cs.alignment`.
- Evidence from the probe point: with X‖a, the crystal direction ∥ A3 is 34.1° from c with an in-plane azimuth of
  8.4° (folded to 0–30°). OIM's [5 −2 −3 7] gives 34.0° and 6.6°; the gap is OIM's rounding to integer indices.
  X‖a* would give 21.6°.
- At load, the `CartesianAlignment` line and the backend's crystal frame per phase go into the load step's `frame`
  output (also in the Load section of the EBSD tab and in MCP `load_map` / `get_sample_sheet`). A file that says
  anything else, or a backend frame X‖a*, gives a warning (`ti64app/frame.py: check_alignment`).

## 4 App axes = OIM axes (EDAX setting 2)

g_app = R · g_file, with R = 180° about (1, −1, 0)/√2. The rotation is constant over the map and identical in both
backends. Therefore:

| app | OIM | IPF colours |
|---|---|---|
| x (right) | −A2 | = IPF along A2 |
| y (down) | −A1 | = IPF along A1 |
| z (into the screen) | −A3 | = IPF along A3 = OIM [001] |

- This agrees with the OIM screen: A1 up, A2 left, with Setting 2.
- It is stated in the EBSD tab, section "Maps and IPF key" (text line plus tooltip of "reference direction").
- Check at the probe point, both backends: the app's IPF-X / IPF-Y / IPF-Z colour (TSL key) equals the colour of
  the file orientation along A2 / A1 / A3 within 0.005 (5-decimal Euler angles in the file). The swapped axis
  differs by more than 0.05.

## 5 Verdict

**The orientations, the map frame and the crystal frame match OIM Analysis.** Nothing needed fixing at load:
setting 2 and X‖a are right, and the map is neither mirrored nor rotated. The only open point is a pixel-exact
colour comparison, which needs a screen capture instead of a photo.
