"""Texture input of phase 6.7 (service layer, backend-neutral): which orientations, in which frame, with which weights.

Specimen frame (CLAUDE.md): X = beam axis, Y = thickness (neutral axis at Y = 0), Z = width. The map frame is x right,
y down, z = x cross y (into the screen; EDAX setting 2, docs/orientation_check.md). The map sheet's position says which
specimen axes the map +x / +y point along; R (3x3) maps map-frame vectors to specimen-frame vectors and every
orientation becomes g_specimen = R g_map (crystal -> specimen). Without a position the map frame is kept (axes named by
the map sheet's labels, default x / y / z) and the result says so; in 'approximate' map-axes mode the user's labels name
the map axes and one of them is the build direction (fix 6.7.1). The build direction comes from the sample sheet (specimen frame) or the map sheet (map frame).

Orientation sets: alpha (current map of the map id) or prior beta (newest reconstruction); whole map, an ROI, or all
maps of a sample combined (each rotated into the specimen frame; needs every map's position); weights: per pixel (area
weighted: each point weighs its pixel area) or per grain (mean orientation, weight = grain area). Grain counts are
reported with every result (prior-beta maps hold few grains: texture uncertain).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .project import Project

AXIS_NAMES = {"X": "beam axis", "Y": "thickness", "Z": "width"}
FEW_GRAINS = 100                       # fewer grains (>= ECD_MIN) than this: 'texture uncertain' warning
ECD_MIN_UM = 5.0                       # prior-beta grains counted from this ECD (recon histMinECD)
MAX_POINTS = 400_000                   # per-pixel ODFs: random subsample above this (fixed seed, reported)


class TextureInputError(ValueError):
    """What is missing for a texture result (plain language, names the place to fix it)."""


# ---- frames -----------------------------------------------------------------------------------------------------
def _axis_vec(a: str) -> np.ndarray:
    v = np.zeros(3)
    v["XYZ".index(a[1])] = -1.0 if a[0] == "-" else 1.0
    return v


def quat_from_matrix(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion (a, b, c, d) with a >= 0 (Shepperd)."""
    R = np.asarray(R, float)
    tr = np.trace(R)
    if tr > 0:
        s = 2 * np.sqrt(1 + tr)
        q = [s / 4, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2 * np.sqrt(1 + R[i, i] - R[j, j] - R[k, k])
        q = np.zeros(4)
        q[0] = (R[k, j] - R[j, k]) / s
        q[1 + i] = s / 4
        q[1 + j] = (R[j, i] + R[i, j]) / s
        q[1 + k] = (R[k, i] + R[i, k]) / s
    q = np.asarray(q, float)
    q /= np.linalg.norm(q)
    return q if q[0] >= 0 else -q


def rotate_quats(q: np.ndarray, qR: np.ndarray) -> np.ndarray:
    """g_specimen = R g_map for quaternions (n, 4) (specimen-side rotation)."""
    from .frame import qmul  # noqa: PLC0415

    return qmul(np.asarray(qR, float), np.asarray(q, float))


@dataclass
class SpecimenFrame:
    kind: str                                    # 'specimen' | 'map'
    R: np.ndarray                                # map -> frame (3x3)
    labels: dict[str, str]                       # plot axes: x (east), y (north), z (centre)
    directions: dict[str, tuple[float, float, float]]   # named directions (BD, beam axis, width, ...) in the frame
    text: str
    warnings: list[str] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)   # direction name -> what to enter to resolve it
    defaults: list[str] = field(default_factory=list)          # directions offered by default (IPF / elastic)
    axis_labels: dict[str, str] = field(default_factory=lambda: {"x": "x", "y": "y", "z": "z"})   # map x / y / z
    mode: str = "exact"                          # map sheet 'Map axes': exact | approximate

    @property
    def quat(self) -> np.ndarray:
        return quat_from_matrix(self.R)

    def resolve(self, name: str) -> tuple[float, float, float] | None:
        """A named direction; the old names 'map x' / 'map y' / 'map z' (recipes before fix 6.7.1) mean the map axes."""
        if name in self.directions:
            return self.directions[name]
        if name in ("map x", "map y", "map z"):
            return tuple(float(c) for c in self.R @ np.eye(3)["xyz".index(name[-1])])
        return None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "mode": self.mode, "R_map_to_frame": np.round(self.R, 6).tolist(),
                "labels": self.labels, "axis_labels": self.axis_labels,
                "directions": {k: [round(c, 6) for c in v] for k, v in self.directions.items()},
                "unavailable": self.unavailable, "text": self.text, "warnings": self.warnings,
                "quat": np.round(self.quat, 8).tolist()}


ENTER_BD = "enter the build direction (Files tab > map sheet > Build direction, or mark an axis as BD in 'Map axes')"
ENTER_POS = "enter the map's position on the specimen (Files tab > map sheet > Position on the specimen, mode exact)"


def _unit(k: str) -> tuple[float, float, float]:
    return tuple(float(c) for c in np.eye(3)["xyz".index(k)])


def frame_for(project: Project, map_id: str) -> SpecimenFrame:
    """Frame of the texture / elastic results of one map (fix 6.7.1 items 2 and 9). Never fails for missing input:
    the map axes are always available (named by the map sheet's axis labels, default x / y / z); directions that
    cannot be resolved yet are listed in `unavailable` with what to enter.
      exact + position     specimen frame (map sheet position + sample sheet BD): beam axis, thickness, width, BD
      exact, no position   map frame: the map axes + BD if entered on the map sheet
      approximate          map frame, axes named by the user's labels, BD = the axis marked as BD"""
    from .sample import MapAxes  # noqa: PLC0415

    sheet = project.sample(map_id)
    m = project.file(map_id)
    s = project.sample_info(m.sample_id) if m is not None and m.sample_id else None
    bd_spec = getattr(s, "build_direction_specimen", None) if s is not None else None
    ax = sheet.axes or MapAxes()
    names = ax.labels
    warnings: list[str] = []
    unavailable: dict[str, str] = {}
    if ax.mode == "approximate":
        R = np.eye(3)
        labels = {k: names[k] + (" = BD" if ax.build_axis == k and names[k] != "BD" else "") for k in "xyz"}
        dirs = {names[k]: _unit(k) for k in "xyz"}
        if ax.build_axis is not None:
            if names[ax.build_axis] != "BD":
                dirs["BD"] = dirs[names[ax.build_axis]]
        else:
            unavailable["BD"] = "mark one axis as the build direction (Files tab > map sheet > Map axes)"
        text = f"map axes named on the map sheet (approximate): {ax.text()}"
        return SpecimenFrame("map", R, labels, dirs, text, warnings, unavailable, [names[k] for k in "xyz"], names,
                             "approximate")
    if sheet.position is not None:
        ex, ey = _axis_vec(sheet.position.map_x_axis), _axis_vec(sheet.position.map_y_axis)
        R = np.column_stack([ex, ey, np.cross(ex, ey)])      # columns: images of map x, y, z
        labels = {}
        for k, a in zip("xyz", ("X", "Y", "Z")):
            labels[k] = f"{AXIS_NAMES[a]} ({a})"
        dirs = {f"{AXIS_NAMES[a]}": tuple(_axis_vec("+" + a)) for a in "XYZ"}
        if bd_spec:
            dirs["BD"] = tuple(_axis_vec(bd_spec))
            for k, a in zip("xyz", "XYZ"):
                if bd_spec[1] == a:
                    labels[k] += " = BD"
        elif sheet.build_direction is not None:
            dirs["BD"] = tuple(R @ np.asarray(sheet.build_direction.unit_vector))
        else:
            unavailable["BD"] = ENTER_BD
            warnings.append("build direction not entered (sample sheet 'build direction in the specimen frame' or the "
                            "map sheet): no BD direction")
        defaults = (["BD"] if "BD" in dirs else ["thickness"]) + ["beam axis", "width"]
        text = (f"map frame -> specimen frame: map +x = {sheet.position.map_x_axis}, map +y = "
                f"{sheet.position.map_y_axis}, map z (into the screen) = "
                f"{_axis_name(np.cross(ex, ey))} (map sheet 'Position on the specimen')")
        mapped = {k: AXIS_NAMES[_axis_name(R[:, i])[1]] for i, k in enumerate("xyz")}
        return SpecimenFrame("specimen", R, labels, dirs, text, warnings, unavailable, defaults, mapped, "exact")
    R = np.eye(3)
    custom = names != {"x": "x", "y": "y", "z": "z"}
    labels = {k: names[k] if custom else f"{k} ({w})" for k, w in zip("xyz", ("right", "down", "into the screen"))}
    dirs = {names[k]: _unit(k) for k in "xyz"}
    defaults = [names[k] for k in "xyz"]
    if sheet.build_direction is not None:
        bd = tuple(float(c) for c in sheet.build_direction.unit_vector)
        along_axis = any(abs(abs(float(np.dot(bd, v))) - 1) < 1e-6 for v in dirs.values())
        dirs["BD"] = bd
        if not along_axis:
            defaults = ["BD"] + defaults                       # BD not along one of the map axes: offered too
    else:
        unavailable["BD"] = ENTER_BD
        warnings.append("build direction not entered (Files tab, map sheet): no BD direction")
    for a in ("beam axis", "thickness", "width"):
        unavailable[a] = ENTER_POS
    warnings.append("the map's position on the specimen is not entered (Files tab, map sheet 'Position on the "
                    "specimen'): axes are the map axes " + " / ".join(names.values()) + ", not beam axis / thickness / "
                    "width")
    return SpecimenFrame("map", R, labels, dirs, "map frame kept (no position on the specimen entered)", warnings,
                         unavailable, defaults, names, "exact")


def direction_choices(project: Project, map_id: str) -> list[dict]:
    """Directions offered for inverse pole figures and E(direction) of a map, in display order:
    [{name, available, vector | None, why (what to enter; '' if available), default}], 'custom' last."""
    try:
        fr = frame_for(project, map_id)
    except (KeyError, ValueError) as e:
        return [{"name": "custom", "available": True, "vector": None, "why": "", "default": False, "error": str(e)}]
    out = [{"name": n, "available": True, "vector": list(v), "why": "", "default": n in fr.defaults}
           for n, v in fr.directions.items()]
    out += [{"name": n, "available": False, "vector": None, "why": why, "default": False}
            for n, why in fr.unavailable.items()]
    out.append({"name": "custom", "available": True, "vector": None, "why": "", "default": False})
    return out


def pick_directions(fr: SpecimenFrame, names: list[str] | None, custom, notes: list[str]) -> list[tuple[str, tuple]]:
    """[(name, vector)] for IPFs / E(direction). names None = the frame's defaults. Never fails for missing input
    (fix 6.7.1 item 2): a direction that cannot be resolved yet is skipped with a note saying what to enter; if none
    is left, the defaults (the map axes) are used."""
    out: list[tuple[str, tuple]] = []
    for n in (fr.defaults if names is None else names):
        if n == "custom":
            if custom is None:
                notes.append("custom direction: no vector entered, skipped")
            else:
                out.append((f"custom {tuple(round(c, 3) for c in custom)}", tuple(float(c) for c in custom)))
            continue
        v = fr.resolve(n)
        if v is not None:
            out.append((n, v))
        else:
            notes.append(f"direction '{n}' skipped: {fr.unavailable.get(n, 'not available in this frame (' + ', '.join(fr.directions) + ')')}")
    if not out:
        notes.append("none of the chosen directions is available: the default directions " + ", ".join(fr.defaults)
                     + " are used")
        out = [(n, fr.directions[n]) for n in fr.defaults]
    return out


def _axis_name(v) -> str:
    i = int(np.argmax(np.abs(v)))
    return ("-" if v[i] < 0 else "+") + "XYZ"[i]


# ---- orientation sets -------------------------------------------------------------------------------------------
@dataclass
class OrientationSet:
    phase: str                                   # 'alpha' | 'beta'
    quat: np.ndarray                             # (n, 4) in the frame
    weights: np.ndarray                          # (n,) area weights (um^2)
    frame: SpecimenFrame
    n_points: int                                # measurements used (before subsampling)
    n_grains: int | None                         # grains touched by the region
    n_grains_ecd: int | None                     # of them with ECD >= ECD_MIN_UM
    area_um2: float
    maps: list[str]
    subsampled: int | None = None
    grain_quat: np.ndarray | None = None         # (g, 4) grain mean orientations in the frame (bootstrap)
    grain_weights: np.ndarray | None = None      # (g,) grain areas inside the region
    warnings: list[str] = field(default_factory=list)
    sources: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"phase": self.phase, "n_orientations": int(len(self.weights)), "n_points": self.n_points,
                "n_grains": self.n_grains, f"n_grains_ecd_ge_{ECD_MIN_UM:g}um": self.n_grains_ecd,
                "area_um2": round(self.area_um2, 1), "maps": self.maps, "subsampled_to": self.subsampled,
                "frame": self.frame.as_dict(), "warnings": self.warnings, "sources": self.sources}


def _npz(project: Project, step_id: str, output: str) -> dict:
    s = project.recipe().step(step_id)
    with np.load(project.root / s.outputs[output].path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _in_roi(project: Project, roi_id: str, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    from .annotations import ROI_TYPES, find  # noqa: PLC0415

    a = find(project, roi_id)
    if a.type not in ROI_TYPES:
        raise TextureInputError(f"annotation {roi_id} is not an ROI")
    xy = np.asarray(a.xy, float)
    if a.type == "roi_rect":
        x0, x1 = sorted((xy[0, 0], xy[1, 0]))
        y0, y1 = sorted((xy[0, 1], xy[1, 1]))
        return (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    from matplotlib.path import Path as MplPath  # noqa: PLC0415

    return MplPath(xy).contains_points(np.column_stack([x, y]))


def _pixel_area(x: np.ndarray, y: np.ndarray) -> float:
    ys = np.unique(np.round(y, 4))
    dy = float(np.median(np.diff(ys))) if ys.size > 1 else 1.0
    xs = np.sort(x[np.abs(y - ys[0]) < dy / 4])
    dx = float(np.median(np.diff(xs))) if xs.size > 1 else 1.0
    return dx * dy


def map_set(project: Project, map_id: str, phase: str, weighting: str, roi_id: str | None = None,
            source: str | None = None, grains: str | None = None, recon: str | None = None) -> tuple[OrientationSet, dict]:
    """Orientations of one map in its specimen frame. Returns (set, resolved step ids)."""
    from .layers import newest_grains  # noqa: PLC0415
    from .ops_ebsd import alpha_id, map_phase_names  # noqa: PLC0415
    from .phases import BETA  # noqa: PLC0415
    from .recipe import current_map_step  # noqa: PLC0415
    from .service import latest_step  # noqa: PLC0415

    steps = project.recipe().steps
    fr = frame_for(project, map_id)
    used: dict = {}
    warn: list[str] = list(fr.warnings)
    if phase in ("alpha", "beta_retained"):
        src = source or getattr(current_map_step(steps, map_id), "id", None)
        if src is None:
            raise TextureInputError(f"map {map_id} is not loaded yet (EBSD tab)")
        used["source"] = src
        d = _npz(project, src, "ebsd")
        if phase == "alpha":
            pid = alpha_id(project, map_id)
        else:
            pid = next((k for k, v in map_phase_names(project, map_id).items() if v == BETA), None)
            if pid is None:
                raise TextureInputError(f"{map_id} has no retained beta: the file has no beta phase (not in file)")
        gid = (grains or newest_grains(steps, map_id, "alpha")) if phase == "alpha" else None
        gtab = None
        if gid:
            used["grains"] = gid
            gz = _npz(project, gid, "grains")
            gtab = {"pix": gz["pixel_grain_id"], "id": gz["grain_id"], "q": gz["grain_mean_quat"],
                    "ecd": gz["grain_ecd_um"], "phase": gz["grain_phase"]}
    else:
        rid = recon or getattr(latest_step(project, ("recon.run",), map_id=map_id), "id", None)
        if rid is None:
            raise TextureInputError(f"map {map_id} has no prior-beta reconstruction yet (section 'Prior-β "
                                    "reconstruction')")
        used["recon"] = rid
        d = _npz(project, rid, "ebsd")
        names = map_phase_names(project, map_id)
        pid = next((k for k, v in names.items() if v == BETA), None)
        if pid is None:          # file without beta: the reconstruction ADDED the parent phase (fix 6.7.1 item 1)
            st = d.get("prop_recon_state")
            ph_t = d["phase"][st == 2] if st is not None else np.zeros(0, int)
            pid = int(np.bincount(ph_t.astype(np.int64)).argmax()) if ph_t.size else max(names, default=1) + 1
        rz = _npz(project, rid, "recon")
        pix = d.get("prop_parent_grain")
        gtab = None
        if pix is not None:
            gtab = {"pix": pix.astype(np.int64), "id": rz["grain_id"], "q": rz["grain_mean_quat"],
                    "ecd": rz["grain_ecd_um"], "phase": np.full(len(rz["grain_id"]), pid)}
    x, y, q, ph = d["x"], d["y"], d["quat"], d["phase"]
    sel = (ph == pid) & np.isfinite(q[:, 0])
    if roi_id:
        sel &= _in_roi(project, roi_id, x, y)
        used["roi"] = roi_id
    if not sel.any():
        raise TextureInputError(f"no {'α' if phase == 'alpha' else 'prior-β'} points in "
                                f"{'the ROI' if roi_id else 'the map'} of {map_id}")
    pa = _pixel_area(x, y)
    n_g = n_g5 = None
    gq = gw = None
    if gtab is not None:
        pix = gtab["pix"][sel]
        ids, cnt = np.unique(pix[pix > 0], return_counts=True)
        lut = {int(g): k for k, g in enumerate(gtab["id"])}
        keep = [i for i in ids if int(i) in lut and gtab["phase"][lut[int(i)]] == pid]
        idx = np.array([lut[int(i)] for i in keep], int)
        cnt = np.array([c for i, c in zip(ids, cnt) if int(i) in lut and gtab["phase"][lut[int(i)]] == pid])
        n_g = int(len(idx))
        n_g5 = int((gtab["ecd"][idx] >= ECD_MIN_UM).sum()) if n_g else 0
        ok = np.isfinite(gtab["q"][idx, 0]) if n_g else np.zeros(0, bool)
        gq = rotate_quats(gtab["q"][idx][ok], fr.quat)
        gw = cnt[ok] * pa
    elif weighting == "grain" and phase != "beta_retained":
        raise TextureInputError(f"one point per grain needs grains of {map_id}: run 'Grains and boundaries' first")
    elif phase != "beta_retained":
        warn.append("no grains computed: number of grains unknown (run 'Grains and boundaries')")
    if weighting == "grain" and gq is not None:
        quat, w = gq, gw
    else:
        quat = rotate_quats(q[sel], fr.quat)
        w = np.full(int(sel.sum()), pa)
    if n_g5 is not None and n_g5 < FEW_GRAINS and phase != "beta_retained":
        warn.append(f"only {n_g5} {'prior-β ' if phase == 'beta' else ''}grains ≥ {ECD_MIN_UM:g} µm: texture "
                    "statistics are uncertain")
    os_ = OrientationSet(phase, quat, w, fr, int(sel.sum()), n_g, n_g5, float(sel.sum() * pa), [map_id],
                         grain_quat=gq, grain_weights=gw, warnings=warn, sources=used)
    return os_, used


def combine(sets: list[OrientationSet]) -> OrientationSet:
    """All maps of a sample in one set (every one rotated into the specimen frame)."""
    bad = [s.maps[0] for s in sets if s.frame.kind != "specimen"]
    if bad:
        raise TextureInputError(f"maps without a position on the specimen cannot be combined: {', '.join(bad)} "
                                "(Files tab, map sheet 'Position on the specimen')")
    fr = sets[0].frame
    cat = lambda k: np.concatenate([getattr(s, k) for s in sets if getattr(s, k) is not None])  # noqa: E731
    have_g = all(s.grain_quat is not None for s in sets)
    sm = lambda k: sum(getattr(s, k) for s in sets) if all(getattr(s, k) is not None for s in sets) else None  # noqa
    warn = [w for s in sets for w in s.warnings if "texture statistics" not in w]
    out = OrientationSet(sets[0].phase, cat("quat"), cat("weights"), fr, sum(s.n_points for s in sets),
                         sm("n_grains"), sm("n_grains_ecd"), sum(s.area_um2 for s in sets),
                         [m for s in sets for m in s.maps],
                         grain_quat=cat("grain_quat") if have_g else None,
                         grain_weights=cat("grain_weights") if have_g else None, warnings=warn,
                         sources={s.maps[0]: s.sources for s in sets})
    if out.n_grains_ecd is not None and out.n_grains_ecd < FEW_GRAINS:
        out.warnings.append(f"only {out.n_grains_ecd} grains ≥ {ECD_MIN_UM:g} µm: texture statistics are uncertain")
    return out


def subsample(s: OrientationSet, max_points: int = MAX_POINTS, seed: int = 0) -> OrientationSet:
    if len(s.weights) <= max_points:
        return s
    idx = np.sort(np.random.default_rng(seed).choice(len(s.weights), max_points, replace=False))
    s.quat, s.weights, s.subsampled = s.quat[idx], s.weights[idx], max_points
    return s


# ---- pole figure / IPF definitions ----------------------------------------------------------------------------
PF_ALPHA = {"{0001}": (0, 0, 0, 1), "{10-10}": (1, 0, -1, 0), "{11-20}": (1, 1, -2, 0), "{10-11}": (1, 0, -1, 1)}
PF_BETA = {"{100}": (1, 0, 0), "{110}": (1, 1, 0), "{111}": (1, 1, 1)}


def poles_for(phase: str, wanted: list[str] | None) -> dict[str, tuple]:
    table = PF_ALPHA if phase == "alpha" else PF_BETA
    if not wanted:
        return dict(table)
    bad = [w for w in wanted if w not in table]
    if bad:
        raise TextureInputError(f"poles {bad} are not {'alpha' if phase == 'alpha' else 'beta'} poles; choose from "
                                f"{list(table)}")
    return {w: table[w] for w in wanted}


def equal_area_grid(n_r: int = 31, n_phi: int = 121) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Upper-hemisphere directions on an equal-area polar grid (Lambert, r = sqrt(2) sin(rho/2), scaled to 1 at the
    rim). Returns (directions (n_r*n_phi, 3), X, Y) with X / Y the plot coordinates (n_r, n_phi)."""
    rr = np.linspace(0, 1, n_r)
    ph = np.linspace(0, 2 * np.pi, n_phi)
    Rr, Ph = np.meshgrid(rr, ph, indexing="ij")
    rho = 2 * np.arcsin(np.clip(Rr / np.sqrt(2), 0, 1))
    d = np.stack([np.sin(rho) * np.cos(Ph), np.sin(rho) * np.sin(Ph), np.cos(rho)], -1)
    return d.reshape(-1, 3), Rr * np.cos(Ph), Rr * np.sin(Ph)
