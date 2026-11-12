"""Hover explanations in plain words for every option of the edit menu (EBSD tab), the sheets (Files tab) and
the stitching options. Defaults come from CLAUDE.md and docs/beta_reconstruction_method.md."""

TIPS: dict[str, str] = {
    # cleanup
    "ci_threshold": "CI = confidence index: how sure the indexing software was about each point (0 to 1). "
                    "Points below this value are removed (set to not indexed). Empty = preset from the input "
                    "state: 0.1 for raw maps, no CI filter for OIM-cleaned maps.",
    "noise_angle_deg": "Angle used to group points into tiny grains when looking for noise. Points that differ "
                       "by more than this are in different grains (EBSD_part2 cleanAngle, default 3°).",
    "noise_max_indexed_px": "Indexed 'grains' this small (in points) are treated as noise and removed "
                            "(default 2).",
    "noise_max_void_px": "Not-indexed holes up to this size (in points) are filled from their neighbours "
                         "(default 4).",
    "fill": "Fill the removed noise and small holes from the neighbouring grains.",
    "spike_removal": "Remove single wild points whose orientation differs from all neighbours by more than "
                     "the spike angle.",
    "spike_angle_deg": "A single point that differs from its neighbours by more than this is a wild spike.",
    "hq_filter": "Smooth orientation noise inside grains (MTEX halfQuadraticFilter). Off by default: it "
                 "changes KAM / GND values.",
    "hq_alpha": "Strength of the smoothing filter (larger = smoother).",
    "hq_threshold_deg": "The filter never smooths across boundaries larger than this angle.",
    # map / IPF
    "layer": "What the map shows: IPF orientation colours, phase, CI, IQ (image quality) or a computed map "
             "(KAM, GROD, GOS, GND, reconstruction).",
    "ipf_key": "IPF colour key: how orientations become colours. HKL = as in the Research Assignment, TSL = "
               "like OIM Analysis, HSV = MTEX default, Spot = only near chosen directions.",
    "ipf_dir": "Reference direction of the IPF colours: X, Y, Z of the map, the build direction from the "
               "sample sheet, or a custom vector.",
    "clim": "Colour range of value maps (CI, IQ, KAM ...). Empty = automatic.",
    "opacity": "Transparency of the map (lets annotations and overlays stand out).",
    "line_width": "Width of the boundary overlay lines on screen (pixels).",
    # grains
    "hagb_deg": "High-angle grain boundary: neighbours misoriented more than this belong to different grains "
                "(default 15°).",
    "lagb_deg": "Low-angle boundary: drawn between neighbours misoriented more than this but less than the "
                "high-angle limit (default 2°).",
    "alpha": "MTEX grain-reconstruction 'alpha' (how readily not-indexed gaps are bridged; default 3.1).",
    "grain_min_px": "Grains smaller than this number of points are merged into their neighbours.",
    "burgers_tol_deg": "Boundaries within this angle of an ideal Burgers α/α misorientation are marked as "
                       "Burgers boundaries (default 3°).",
    # KAM / GND
    "kam_kind": "KAM = kernel average misorientation (local strain), GROD = deviation from the grain mean, "
                "GOS = grain orientation spread, GND = geometrically necessary dislocation density.",
    "kam_order": "How many rings of neighbours KAM averages over (1 = nearest neighbours only).",
    "kam_threshold_deg": "Neighbours misoriented more than this are ignored in KAM (they are grain "
                         "boundaries, not strain; default 5°).",
    "gnd_stencil": "Neighbours used for the curvature in GND: 1hop = nearest, oneSided / full = wider stencils.",
    "poisson": "Poisson's ratio of Ti-6Al-4V used by the GND estimate (0.34).",
    # reconstruction
    "method": "variantGraph = MTEX variant-graph method (Niessen 2022, Hielscher 2022, default); graph = older "
              "grain-graph clustering.",
    "vgThreshold": "Largest misfit (°) between α grains and a common β parent for them to be grouped. "
                   "Yang 2025: 5-8°; default 5°.",
    "vgTolerance": "Tolerance (°) of the variant graph: how far a boundary may deviate from an ideal Burgers "
                   "relation (default 1.5°).",
    "segAngle": "Angle that splits the α map into grains before reconstruction (default 1.5°).",
    "orMode": "Orientation relationship: refined OR (fitted to this map) if it is within the limit of Burgers, "
              "otherwise ideal Burgers.",
    "orMaxDev": "Use the refined OR only if it is at most this far from ideal Burgers (default 1°). The "
                "deviation is reported.",
    "mergeMode": "How similar neighbouring parent grains are merged after reconstruction: once at one angle "
                 "(single) or in steps (progressive).",
    "mergeThreshold": "Neighbouring prior-β grains closer than this are merged (Yang 2025: ~3°).",
    "inclusionMaxSize": "Prior-β grains smaller than this (in points) inside another grain are merged into it.",
    "sweep_thr": "Thresholds to try in the parameter sweep (comma-separated, °).",
    "sweep_merge": "Merge thresholds to try in the sweep (comma-separated, °).",
    "recon_fig": "Which reconstruction figure to draw with the backend.",
    # grain size
    "gs_hagb": "Boundary angle that separates α laths for the grain-size measurement (15° keeps 10.53° packet "
               "pairs in one grain).",
    "gs_min_px": "Grains smaller than this number of points are not counted (ISO 13067).",
    "exclude_edge": "Do not count grains that touch the map edge (their size is unknown; ISO 13067 / ASTM E2627).",
    "lath_min_aspect": "Only grains at least this elongated count as laths for the thickness measurement.",
    "colony_tol": "α grains whose misorientation is within this angle are grouped into one colony.",
    "beta_line_spacing_um": "Spacing of the test lines for prior-β width / length (µm).",
    "gs_fig": "Which grain-size figure to draw with the backend.",
    "gs_range": "Smallest and largest grain size shown in the histogram (µm; empty = from 0 / up to the largest grains). Grains outside are not drawn (the title says how many); the statistics and the log-normal fit stay those of all grains.",
    "gs_bin": "Histogram bin width in µm (empty = automatic, Freedman-Diaconis). The log-normal fit is drawn as "
              "before; only the bars change.",
    "gs_oim": "Grain file exported from OIM Analysis, to compare the grain sizes with OIM.",
    # files / sheets
    "sample_id": "Specimen ID, e.g. JU04. Output folders and export names start with it.",
    "material": "Material of the specimen (e.g. Ti-6Al-4V).",
    "process": "How the specimen was made (e.g. EBM).",
    "heat_treatment": "Heat treatment after building, if any (e.g. HIP 920 °C / 2 h).",
    "bending": "4-point bending: outer and inner roller span, specimen width and thickness (mm) and the load (N). "
               "Enter all four sizes or none.",
    "bd_specimen": "Build direction in the specimen frame (X = beam axis, Y = thickness, Z = width).",
    "tension_side": "Which face of the specimen is in tension (+Y or -Y). Used to suggest each map's role.",
    "input_state": "raw = straight from indexing; OIM-cleaned = already cleaned in OIM Analysis. Selects the "
                   "cleanup preset; changing it marks this map's results stale.",
    "phases": "Which phase of the .ang is α and which is β. Found from the crystal symmetry (hexagonal 6/mmm = α, "
              "cubic m-3m = β), never from the name (APEX writes 'Ti (Titanium, alpha)', older OIM 'Ti (alpha)'). "
              "Change a role only when the symmetry does not decide; this marks the map's results stale.",
    "prias": "PRIAS images exported from OIM as text next to the .ang (<map>_prias.txt: x y value per point). "
             "Shown as grey maps in the 'Maps' list of the EBSD tab and used as the reference image when BSE / crack "
             "images are registered to the map (else the IQ map).",
    "bd_map": "Build direction in MAP coordinates (x right, y down): angle from +x toward +y (clockwise on "
              "screen), or a vector. Not needed to load the map; steps that use it ask for it. Never guessed.",
    "position": "Where the map's first pixel sits on the specimen (sample frame, mm) and which specimen axes "
                "the map +x / +y point along.",
    "role": "Which part of the bent specimen the map shows: tension, middle (near the neutral axis) or "
            "compression. Suggested from the position.",
    "notes": "Free notes (kept with the project).",
    # stitch
    "st_detector": "Which detector's images to stitch. BSE and SE are never mixed in one mosaic.",
    "st_se_layout": "Register the BSE images and place the SE images at the same positions (aligned mosaics).",
    "st_strip": "The JEOL data bar under each image: none (pixels only), keep the original (single column) or "
                "generate a new one for the mosaic.",
    "st_blend": "How overlapping tiles are combined. Narrow band (default): each pixel comes from one tile, mixed "
                "only in a band around the seam, so a tiny misalignment cannot show as double edges across the "
                "whole overlap. Whole overlap: smooth feathering (older behaviour). No blending: raw pixel values "
                "for measurements.",
    "st_seam": "Where one tile ends and the next begins: the middle of the overlap, or a path through the overlap "
               "where the two tiles differ least (hides the seam in uniform areas).",
    "st_band": "Width of the blending band around a seam, in pixels (0 = hard cut).",
    "st_radius": "Refine searches each match only this far from your arrangement (percent of the tile size).",
    "st_refine": "Register the ticked images starting from the positions in this view: matches are searched only "
                 "within the refine radius; locked tiles stay where you put them. The arrangement is saved in the "
                 "recipe.",
    "st_sets": "Images that can be stitched together: same magnification and pixel size, and stage positions whose "
               "fields of view overlap. Different magnifications are never stitched together.",
    "st_no_stage": "Ignore the stage positions in the .txt files (e.g. a faulty stage, or all images saved with the "
                   "same position): the tiles are placed only by image matching of every pair, or from your positions "
                   "on the 'Arrange' tab once you moved a tile there. Sets are then not split by stage position.",
    "st_min_score": "Matches with a lower normalised cross-correlation are not trusted; such tiles are placed "
                    "from the stage position (red border).",
}


def tip(key: str) -> str:
    return TIPS.get(key, "")
