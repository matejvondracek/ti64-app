"""EBSD operations of the service layer (phase 2): cleanup, grains, maps, boundaries, figures.

Every op works on a SOURCE map: by default the newest non-stale cleanup of the map, otherwise its
load step; `source` (a step id) overrides that. The resolved source and preset are recorded in the
step params, so replay repeats exactly the same chain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .backends.base import (BURGERS_TYPES, CleanupParams, GrainParams, MapParams, RenderSpec,
                            cleanup_params_for)
from . import progress
from .phases import ALPHA, BETA
from .service import OpContext, latest_step, op
from .settings import IpfKeySetting

MAP_SOURCES = ("ebsd.cleanup", "ebsd.load")


def resolve_source(ctx: OpContext, map_id: str, source: str | None, ops: tuple[str, ...] = MAP_SOURCES) -> str:
    if source:
        return source
    s = latest_step(ctx.project, ops, map_id=map_id)
    if s is None:
        raise KeyError(f"map {map_id!r} is not loaded yet: run ebsd.load first")
    return s.id


# ---- cleanup ----------------------------------------------------------------------------------
class EbsdCleanupParams(BaseModel):
    map_id: str
    source: str | None = None                 # load step id (default: newest ebsd.load of the map)
    overrides: dict[str, Any] = Field(default_factory=dict)   # CleanupParams fields to change
    preset: str | None = None                 # filled in: the input state that selected the preset
    resolved: dict[str, Any] | None = None    # filled in: the CleanupParams actually used


@op("ebsd.cleanup", EbsdCleanupParams, uses_backend=True)
def _ebsd_cleanup(ctx: OpContext) -> None:
    """Cleanup with the preset of the map's input state (raw / OIM-cleaned) plus overrides:
    CI threshold, wild-spike removal, noise removal (EBSD_part2.m), grain-dilation-like fill,
    optional halfQuadraticFilter. The loaded map is not changed; a new map is produced."""
    p = EbsdCleanupParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    src = resolve_source(ctx, p.map_id, p.source, ("ebsd.load",))
    unknown = set(p.overrides) - set(CleanupParams.model_fields)
    if unknown:
        raise ValueError(f"unknown cleanup parameters: {sorted(unknown)}")
    cp = cleanup_params_for(sheet.input_state, p.overrides)
    h = ctx.use(src)
    # materialize (viewer / later step needs the cleaned map object after an app restart): the cleanup runs again
    progress.report("repeating cleanup" if ctx.need_object else "cleanup", 0.5)
    new, rep = ctx.backend.cleanup(h, cp)
    ctx.remember(new)
    data = ctx.backend.get_ebsd(new)
    ctx.params.update(source=src, preset=sheet.input_state, resolved=cp.model_dump(mode="json"))
    ctx.metrics.update(n_filled=rep.n_filled, n_indexed_after=rep.n_indexed_after)
    ctx.output("ebsd", "npz", data)
    ctx.output("report", "json", rep.model_dump(mode="json"))


# ---- shared helpers ---------------------------------------------------------------------------
class DataUnavailable(ValueError):
    """The map does not contain the requested data (e.g. Fit or PRIAS in the test exports)."""


def step_npz(ctx: OpContext, step_id: str, output: str) -> dict[str, np.ndarray]:
    s = ctx.project.recipe().step(step_id)
    with np.load(ctx.project.root / s.outputs[output].path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


class BoundaryParams(BaseModel):
    burgers_tol_deg: float = 3.0              # user decision 2026-10-31


def classify_boundaries(gb: dict[str, np.ndarray], gp: GrainParams, bp: BoundaryParams,
                        alpha_phase: int = 1) -> dict[str, np.ndarray]:
    """Boolean masks per boundary class (a segment can be HAGB and a Burgers type at the same time).
    edge: map border; interface: two different indexed phases; hagb / lagb: same indexed phase by
    misorientation; burgers_k: alpha/alpha within tol of Burgers type k (1..5 = BURGERS_TYPES)."""
    inside = (gb["grain1"] > 0) & (gb["grain2"] > 0)
    p1, p2 = gb["phase1"], gb["phase2"]
    same = inside & (p1 == p2) & (p1 > 0)
    mis = np.nan_to_num(gb["mis_deg"], nan=-1.0)
    out = {"edge": ~inside,
           "interface": inside & (p1 != p2) & (p1 > 0) & (p2 > 0),
           "hagb": same & (mis >= gp.hagb_deg),
           "lagb": same & (mis >= gp.lagb_deg) & (mis < gp.hagb_deg)}
    aa = same & (p1 == alpha_phase)
    ok = aa & (np.nan_to_num(gb["burgers_dist_deg"], nan=1e9) <= bp.burgers_tol_deg)
    for k in range(1, len(BURGERS_TYPES) + 1):
        out[f"burgers_{k}"] = ok & (gb["burgers_type"] == k)
    out["burgers_other"] = aa & ~ok & (mis >= gp.lagb_deg)
    return out


GB_STYLE = {"lagb": ("LAGB 2-15°", (0.65, 0.65, 0.65)), "hagb": ("HAGB ≥15°", (0.0, 0.0, 0.0)),
            "interface": ("α/β interface", (0.85, 0.1, 0.1))}
BURGERS_RGB = [(0.12, 0.47, 0.71), (1.0, 0.5, 0.05), (0.17, 0.63, 0.17), (0.84, 0.15, 0.16), (0.58, 0.4, 0.74)]
PHASE_RGB = {ALPHA: (0.53, 0.81, 0.98), BETA: (0.56, 0.74, 0.56)}    # MTEX default LightSkyBlue / DarkSeaGreen
OTHER_PHASE_RGB = (0.75, 0.75, 0.75)


def map_phase_names(project, map_id: str) -> dict[int, str]:
    """{file phase id: name} of a loaded map (names set at load: 'Ti (alpha)' / 'Ti (beta)' by crystal symmetry,
    ti64app.phases); a map loaded before fix 4 keeps the file's own names in its meta, mapped the same way here."""
    load = latest_step(project, ("ebsd.load",), map_id=map_id)
    names: dict[int, str] = {}
    if load is not None and "meta" in load.outputs:
        try:
            meta = json.loads((project.root / load.outputs["meta"].path).read_text(encoding="utf-8"))
            names = {int(k): v for k, v in meta.get("phase_names", {}).items()}
        except (OSError, ValueError):
            names = {}
    if not names:
        return {1: ALPHA, 2: BETA}
    return {k: (v if v in (ALPHA, BETA) else ALPHA if "alpha" in v.lower() and ALPHA not in names.values()
                else BETA if "beta" in v.lower() and BETA not in names.values() else v) for k, v in names.items()}


def alpha_id(project, map_id: str) -> int:
    return next((k for k, v in map_phase_names(project, map_id).items() if v == ALPHA), 1)
BETA_GB_RGB = (0.0, 0.0, 0.0)                                 # prior-beta boundaries: black, 2x width (EBSD_part2)


class OverlayStyle(BaseModel):
    """Line style of one overlay class (viewer + exports): colour override and a width factor (1 = default)."""
    color: str | None = None                  # "#rrggbb"; None = default colour (Burgers types keep their colours)
    width: float = Field(1.0, gt=0, le=20)


def _hex_rgb(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ---- grains -----------------------------------------------------------------------------------
class GrainsCalcParams(BaseModel):
    map_id: str
    source: str | None = None                 # map step (load / cleanup) or a recon.run step = prior-beta grains
    grains: GrainParams = Field(default_factory=GrainParams)
    boundaries: BoundaryParams = Field(default_factory=BoundaryParams)


def grains_target(project, step) -> str:
    """'beta' for a grains.calc step on the reconstructed parent map (source = a recon.run step), else 'alpha'."""
    src = step.params.get("source")
    if not src:
        return "alpha"
    try:
        return "beta" if project.recipe().step(src).op == "recon.run" else "alpha"
    except KeyError:
        return "alpha"


def resolve_grains(ctx: OpContext, map_id: str, grains: str | None, target: str = "alpha") -> str:
    """grains.calc step: the given one, else the newest current one of the map on the alpha map ('alpha') or on the
    prior-beta map ('beta', fix 3.10). Alpha steps (KAM, grain size, alpha overlays) never pick prior-beta grains."""
    if grains:
        return grains
    steps = [s for s in ctx.project.recipe().steps if s.op == "grains.calc" and not s.stale
             and s.params.get("map_id") == map_id and grains_target(ctx.project, s) == target]
    if not steps:
        raise KeyError(f"map {map_id!r} has no {'prior-β ' if target == 'beta' else ''}grains yet: run "
                       f"'Grains and boundaries'{' on the prior β tab' if target == 'beta' else ''} first")
    return steps[-1].id


@op("grains.calc", GrainsCalcParams, uses_backend=True)
def _grains_calc(ctx: OpContext) -> None:
    """Grain segmentation (HAGB 15°, LAGB 2° inside grains by default) and the boundary table with misorientation
    and nearest Burgers alpha/alpha type per segment: alpha grains of a loaded / cleaned map, or prior-beta grains of
    the reconstructed parent map when source is a recon.run step (GUI: 'Grains and boundaries' on the prior β tab)."""
    p = GrainsCalcParams.model_validate(ctx.params)
    src = resolve_source(ctx, p.map_id, p.source)
    gh = ctx.backend.calc_grains(ctx.use(src), p.grains)
    ctx.remember(gh)
    gd = ctx.backend.get_grains(gh)
    gb = gd.boundary
    cls = classify_boundaries(gb, p.grains, p.boundaries, alpha_id(ctx.project, p.map_id))
    g = gd.grain
    idx = g["phase"] > 0
    inner = idx & ~g["is_edge"]
    summary = {
        "n_grains": int(len(g["id"])), "n_indexed_grains": int(idx.sum()),
        "n_edge_grains": int((idx & g["is_edge"]).sum()),
        "ecd_um_mean_inner": float(g["ecd_um"][inner].mean()) if inner.any() else None,
        "ecd_um_area_weighted_inner": float((g["ecd_um"][inner] * g["area_um2"][inner]).sum()
                                            / g["area_um2"][inner].sum()) if inner.any() else None,
        "boundary_length_um": {k: float(gb["length_um"][m].sum()) for k, m in cls.items()},
        "burgers_types_deg": list(BURGERS_TYPES),
        "params": {"grains": p.grains.model_dump(mode="json"), "boundaries": p.boundaries.model_dump(mode="json")}}
    ctx.params.update(source=src)
    ctx.metrics.update(n_grains=summary["n_grains"])
    ctx.output("grains", "npz", gd.to_arrays())
    ctx.output("summary", "json", summary)


# ---- per-pixel orientation maps ----------------------------------------------------------------
class MapComputeParams(MapParams):
    map_id: str
    grains: str | None = None                 # grains.calc step (default: newest of the map)


@op("map.compute", MapComputeParams, uses_backend=True)
def _map_compute(ctx: OpContext) -> None:
    """KAM (kernel order + cutoff), GROD, GOS or GND per pixel, inside the grains of a grains.calc step."""
    p = MapComputeParams.model_validate(ctx.params)
    gsrc = resolve_grains(ctx, p.map_id, p.grains)
    gh = ctx.use(gsrc, "grains")
    v = ctx.backend.compute_map(gh, MapParams(**{k: getattr(p, k) for k in MapParams.model_fields}))
    ok = ~np.isnan(v)
    q = np.percentile(v[ok], [5, 25, 50, 75, 95]).tolist() if ok.any() else [None] * 5
    ctx.params.update(grains=gsrc)
    ctx.output("values", "npz", {"values": v})
    ctx.output("stats", "json", {"kind": p.kind, "n_valid": int(ok.sum()),
                                 "mean": float(v[ok].mean()) if ok.any() else None,
                                 "p05_p25_p50_p75_p95": q,
                                 "unit": "1/m^2" if p.kind == "gnd" else "deg"})


# ---- figures ----------------------------------------------------------------------------------
FIGURE_KINDS = ("ipf", "phase", "ci", "iq", "fit", "prias", "prias_top", "prias_center", "prias_bottom", "kam", "grod", "gos", "gnd",
                "boundaries", "burgers", "ipf_key")


class FigureParams(BaseModel):
    map_id: str
    kind: Literal["ipf", "phase", "ci", "iq", "fit", "prias", "prias_top", "prias_center", "prias_bottom", "kam", "grod", "gos",
                  "gnd", "boundaries", "burgers", "ipf_key"]
    source: str | None = None                 # map step (load / cleanup)
    grains: str | None = None                 # grains.calc step for overlays (default newest, if any)
    values: str | None = None                 # map.compute step for kam/grod/gos/gnd (default newest)
    phases: list[str] | None = None           # IPF phase filter (default: all indexed phases)
    overlays: list[Literal["hagb", "lagb", "interface", "burgers", "beta_gb", "crack"]] = Field(default_factory=list)
    recon: str | None = None                  # recon.run step for the beta_gb overlay (default newest)
    key: str | None = None                    # IPF key override (default: Settings per phase)
    direction: Literal["X", "Y", "Z", "build", "custom"] | None = None
    custom_vector: tuple[float, float, float] | None = None
    ipf_keys: dict[str, IpfKeySetting] | None = None   # per-phase key / direction (viewer); before key/direction
    clim: tuple[float, float] | None = None
    prias_column: int = 0                     # PRIAS columns in the .ang rows (0 bottom, 1 centre, 2 top), if any


def _ipf_vector(sheet, direction: str, custom) -> tuple[float, float, float]:
    if direction == "X":
        return (1.0, 0.0, 0.0)
    if direction == "Y":
        return (0.0, 1.0, 0.0)
    if direction == "Z":
        return (0.0, 0.0, 1.0)
    if direction == "build":
        from .sample import require_build_direction  # noqa: PLC0415

        return require_build_direction(sheet, "IPF colours along the build direction").unit_vector
    if custom is None:
        raise ValueError("custom IPF direction needs custom_vector")
    return tuple(float(c) for c in custom)


def overlay_segments(ctx: OpContext, map_id: str, overlays: list[str], grains: str | None = None,
                     recon: str | None = None, line_width: float = 1.0, styles: dict | None = None) -> dict:
    """Boundary / crack overlays as vector data (same for figures, viewer and exports).
    hagb / lagb / interface / burgers: boundary table of a grains.calc step (newest of the map by default);
    beta_gb: prior-beta grain boundaries of a recon.run step (newest by default); crack: crack paths drawn as
    annotations. styles: {class: OverlayStyle} colour / width factor per overlay class (GUI settings).
    Returns seg (x1 y1 x2 y2), rgb (m, 3), widths (m,), legend, per-class lengths and the resolved step ids.
    Segments are in draw order (first = bottom)."""
    from .annotations import load_annotations, rgb_of  # noqa: PLC0415

    sty = {k: (v if isinstance(v, OverlayStyle) else OverlayStyle.model_validate(v)) for k, v in (styles or {}).items()}

    def col(ov, default):
        return _hex_rgb(sty[ov].color) if ov in sty and sty[ov].color else default

    def wf(ov):
        return sty[ov].width if ov in sty else 1.0

    parts: list[tuple[dict, np.ndarray, float]] = []          # (seg, rgb (m,3), width)
    legend: list = []
    lengths: dict[str, float] = {}
    used: dict[str, str] = {}
    gb = cls = None
    if any(o in ("hagb", "lagb", "interface", "burgers") for o in overlays):
        gsrc = resolve_grains(ctx, map_id, grains)
        gstep = ctx.project.recipe().step(gsrc)
        gp = GrainParams(**gstep.params["grains"])
        bp = BoundaryParams(**gstep.params["boundaries"])
        ctx.step.inputs.append(f"{gsrc}:grains")
        gz = step_npz(ctx, gsrc, "grains")
        gb = {k[3:]: v for k, v in gz.items() if k.startswith("gb_")}
        cls = classify_boundaries(gb, gp, bp, alpha_id(ctx.project, map_id))
        used["grains"] = gsrc
    # grain-boundary classes: segments in boundary-table order, a segment in several requested classes
    # takes the colour of the LAST one (as the phase-2 figure op, so recorded figures replay identically)
    gclasses = [o for o in overlays if o in ("hagb", "lagb", "interface", "burgers")]
    per_class = len({wf(o) for o in gclasses}) > 1         # different widths: one part per class, in list order
    if gclasses:
        sel = np.zeros(len(gb["x1"]), bool)
        rgb = np.zeros((len(gb["x1"]), 3))
        for ov in gclasses:
            if ov == "burgers":
                ms = np.zeros(len(gb["x1"]), bool)
                for k, ang in enumerate(BURGERS_TYPES, start=1):
                    m = cls[f"burgers_{k}"]
                    rgb[m], sel, ms = BURGERS_RGB[k - 1], sel | m, ms | m
                    legend.append((f"{ang:g}°", BURGERS_RGB[k - 1]))
                    lengths[f"{ang:g}°"] = float(gb["length_um"][m].sum())
            else:
                m = ms = cls[ov]
                c = col(ov, GB_STYLE[ov][1])
                rgb[m], sel = c, sel | m
                legend.append((GB_STYLE[ov][0], c))
                lengths[GB_STYLE[ov][0]] = float(gb["length_um"][m].sum())
            if per_class:
                parts.append(({k: gb[k][ms] for k in ("x1", "y1", "x2", "y2")}, rgb[ms], line_width * wf(ov)))
        if not per_class:
            parts.append(({k: gb[k][sel] for k in ("x1", "y1", "x2", "y2")}, rgb[sel], line_width * wf(gclasses[0])))
    for ov in overlays:
        if ov in gclasses:
            continue
        if ov == "beta_gb":
            rid = resolve_source(ctx, map_id, recon, ("recon.run",))
            ctx.step.inputs.append(f"{rid}:recon")
            rz = step_npz(ctx, rid, "recon")
            b = {k[3:]: v for k, v in rz.items() if k.startswith("gb_")}
            m = (b["grain1"] > 0) & (b["grain2"] > 0)
            c = col("beta_gb", BETA_GB_RGB)
            parts.append(({k: b[k][m] for k in ("x1", "y1", "x2", "y2")}, np.tile(c, (int(m.sum()), 1)),
                          2 * line_width * wf("beta_gb")))
            legend.append(("prior β GB", c))
            lengths["prior β GB"] = float(b["length_um"][m].sum())
            used["recon"] = rid
        elif ov == "crack":
            cr = [a for a in load_annotations(ctx.project, map_id) if a.type == "crack"]
            xs = [np.asarray(a.xy, float) for a in cr]
            seg = {"x1": np.concatenate([x[:-1, 0] for x in xs]) if xs else np.zeros(0),
                   "y1": np.concatenate([x[:-1, 1] for x in xs]) if xs else np.zeros(0),
                   "x2": np.concatenate([x[1:, 0] for x in xs]) if xs else np.zeros(0),
                   "y2": np.concatenate([x[1:, 1] for x in xs]) if xs else np.zeros(0)}
            rgb = (np.concatenate([np.tile(col("crack", rgb_of(a)), (len(a.xy) - 1, 1)) for a in cr]) if cr
                   else np.zeros((0, 3)))
            parts.append((seg, rgb, 2 * line_width * wf("crack")))
            legend.append(("crack", rgb_of(cr[0]) if cr else (0.88, 0.0, 0.0)))
            lengths["crack"] = float(sum(np.hypot(*np.diff(x, axis=0).T).sum() for x in xs))
            used["crack_ids"] = ",".join(a.id for a in cr)
        else:
            raise ValueError(f"unknown overlay {ov!r}")
    if parts:
        seg = {k: np.concatenate([np.asarray(s[k], float) for s, _, _ in parts]) for k in ("x1", "y1", "x2", "y2")}
        rgb = np.concatenate([np.asarray(r, float).reshape(-1, 3) for _, r, _ in parts])
        widths = np.concatenate([np.full(len(s["x1"]), w) for s, _, w in parts])
    else:
        seg, rgb, widths = None, np.zeros((0, 3)), np.zeros(0)
    return {"seg": seg, "rgb": rgb, "widths": widths, "legend": legend, "lengths": lengths, "used": used,
            "parts": parts}


def figure_spec(ctx: OpContext, p: FigureParams):
    """RenderSpec of a map figure (all kinds except ipf_key) + lazy backend handle + meta.
    Shared by the figure op, the map viewer and exports; the caller renders."""
    from .settings import load_settings  # noqa: PLC0415

    sheet = ctx.project.sample(p.map_id)
    style = load_settings().figure
    src = resolve_source(ctx, p.map_id, p.source)
    cache: dict = {}

    def handle():
        if "h" not in cache:
            cache["h"] = ctx.use(src)
        return cache["h"]
    data = step_npz(ctx, src, "ebsd")
    phase = data["phase"]
    names = map_phase_names(ctx.project, p.map_id)
    spec = RenderSpec(width_cm=style.width_cm, dpi=style.dpi, scale_bar=style.scale_bar,
                      seg_width=style.boundary_line_width)
    meta: dict[str, Any] = {"kind": p.kind, "map_id": p.map_id, "input_state": sheet.input_state, "warnings": []}

    if p.kind == "ipf":
        phases = p.phases or [names[k] for k in sorted(names) if (phase == k).any() and names[k] in (ALPHA, BETA)]
        rgb = np.full((phase.size, 3), np.nan)
        keys = {}
        # viewer, source object not alive (app restarted, cache cleared): colour the STORED orientations through any
        # live object of the map (or its load step) instead of rebuilding the source; a cleaned map would otherwise
        # be loaded and cleaned again just to colour it (fix 3.8). Same orientations, same key -> same colours.
        from .layers import _any_map_handle, handle_alive  # noqa: PLC0415

        by_quat = ctx.step.op == "view" and not handle_alive(ctx, src)
        hq = _any_map_handle(ctx, p.map_id) if by_quat else None
        for ph in phases:
            s = (p.ipf_keys or {}).get(ph) or style.ipf.get(ph) or IpfKeySetting()
            key = p.key or s.key
            vec = _ipf_vector(sheet, p.direction or s.direction, p.custom_vector or s.custom_vector)
            keys[ph] = {"key": key, "direction": p.direction or s.direction, "vector": list(vec)}
            if by_quat:
                k = next(n for n, v in names.items() if v == ph)
                m = phase == k
                c = np.full((phase.size, 3), np.nan)
                c[m] = ctx.backend.ipf_colors_quat(hq, ph, data["quat"][m], key, vec)
            else:
                c = ctx.backend.ipf_colors(handle(), ph, key, vec)
            m = ~np.isnan(c[:, 0])
            rgb[m] = c[m]
        meta["keys"] = keys
        spec.rgb = rgb
        spec.title = "IPF " + ", ".join(f"{k}: {v['key']} ∥ {v['direction']}" for k, v in keys.items())
    elif p.kind == "phase":
        rgb = np.full((phase.size, 3), np.nan)
        for k, n in names.items():
            rgb[phase == k] = PHASE_RGB.get(n, OTHER_PHASE_RGB)
        spec.rgb = rgb
        spec.legend = [(n, PHASE_RGB.get(n, OTHER_PHASE_RGB)) for k, n in sorted(names.items()) if (phase == k).any()]
        spec.title = "phase map"
    elif p.kind in ("ci", "iq", "fit"):
        v = data.get(f"prop_{p.kind}")
        if v is None:
            raise DataUnavailable(f"{p.kind.upper()} is not in this map")
        v = v.astype(float)
        if p.kind == "fit" and np.nanmin(v) == np.nanmax(v):
            raise DataUnavailable(f"Fit is constant ({np.nanmin(v):g}) in this .ang: not stored by the export")
        spec.values, spec.cmap = v, "gray" if p.kind == "iq" else style.colormap_scalar
        if p.kind == "ci":
            spec.values = np.where(phase > 0, v, np.nan)
            if sheet.input_state == "oim_cleaned":
                spec.colorbar_label = "CI after OIM cleanup"
                meta["warnings"].append("CI was modified by OIM cleanup (e.g. CI standardisation): "
                                        "not the raw indexing confidence")
            else:
                spec.colorbar_label = "CI (raw indexing confidence)"
        else:
            spec.colorbar_label = {"iq": "IQ", "fit": "Fit (°)"}[p.kind]
        spec.clim = p.clim
        spec.title = spec.colorbar_label
    elif p.kind.startswith("prias"):
        from .io import prias  # noqa: PLC0415

        files = prias.available(sheet.map_file)            # <map>_prias.txt exports next to the .ang (fix 4)
        want = p.kind[6:] or next((k for k in prias.CHANNEL_ORDER if k in files), next(iter(files), ""))
        if want in files:
            try:
                spec.values = prias.values(files[want], sheet.map_file, ctx.project.cache_dir)
            except prias.PriasMismatch as e:
                raise DataUnavailable(str(e)) from None
            ctx.add_input(files[want])
            label = prias.channel(files[want])[1]
        elif p.kind == "prias":                               # PRIAS columns in the .ang rows (none in the exports)
            from .io.ang import read_prias  # noqa: PLC0415

            spec.values = read_prias(sheet.map_file, p.prias_column)
            label = "PRIAS"
        else:
            raise DataUnavailable(f"no {prias.CHANNELS.get(want, 'PRIAS')} export next to the .ang "
                                  f"({Path(sheet.map_file).stem}_prias.txt)")
        if spec.values.size != phase.size:
            raise DataUnavailable(f"PRIAS has {spec.values.size:,} points, the map {phase.size:,}")
        spec.cmap, spec.colorbar_label, spec.title = "gray", label, f"{label} (virtual BSE)"
    elif p.kind in ("kam", "grod", "gos", "gnd"):
        vsrc = p.values or getattr(latest_step(ctx.project, ("map.compute",), map_id=p.map_id, kind=p.kind), "id", None)
        if vsrc is None:
            raise KeyError(f"no {p.kind} computed for {p.map_id}: run map.compute first")
        vstep = ctx.project.recipe().step(vsrc)
        if vstep.backend != ctx.backend.name:
            raise ValueError(f"{p.kind} values were computed by {vstep.backend}; recompute with {ctx.backend.name}")
        ctx.step.inputs.append(f"{vsrc}:values")
        v = step_npz(ctx, vsrc, "values")["values"]
        spec.values, spec.cmap = v, style.colormap_kam if p.kind == "kam" else style.colormap_scalar
        unit = "1/m²" if p.kind == "gnd" else "°"
        spec.colorbar_label = f"{p.kind.upper()} ({unit})"
        if p.kind == "gnd":
            spec.values = np.log10(np.where(v > 0, v, np.nan))
            spec.colorbar_label = "log10 GND (1/m²)"
        spec.clim = p.clim or ((0.0, vstep.params.get("kam_threshold_deg", 5.0)) if p.kind == "kam" else None)
        spec.title = spec.colorbar_label
        if sheet.input_state == "oim_cleaned" and p.kind in ("kam", "gnd"):
            meta["warnings"].append("OIM grain dilation copied neighbour orientations: KAM/GND locally too low")
        ctx.params.update(values=vsrc)
    elif p.kind in ("boundaries", "burgers"):
        iq = data.get("prop_iq")
        spec.values, spec.cmap = (iq.astype(float) if iq is not None else np.zeros(phase.size)), "gray"
        spec.title = "grain boundaries" if p.kind == "boundaries" else "Burgers α/α variant boundaries"
        p.overlays = (["lagb", "hagb", "interface"] if p.kind == "boundaries" else ["burgers"])
    else:
        raise ValueError(f"figure_spec does not draw {p.kind!r}")
    if p.overlays:
        ov = overlay_segments(ctx, p.map_id, list(p.overlays), p.grains, p.recon, style.boundary_line_width)
        spec.seg, spec.seg_rgb = ov["seg"], ov["rgb"]
        if np.unique(ov["widths"]).size > 1:
            spec.seg_widths = ov["widths"]
        spec.legend += ov["legend"]
        if "grains" in ov["used"]:
            ctx.params.update(grains=ov["used"]["grains"])
        if "recon" in ov["used"]:
            ctx.params.update(recon=ov["used"]["recon"])
        meta["boundary_length_um"] = ov["lengths"]
    ctx.params.update(source=src)
    meta["source"] = src
    return spec, handle, meta


@op("figure", FigureParams, uses_backend=True)
def _figure(ctx: OpContext) -> None:
    """A map figure drawn by the active backend: IPF (key + reference direction per phase from
    Settings, phase filter), phase, CI, IQ, Fit, PRIAS, KAM, GROD, GOS, GND, boundary maps
    (LAGB / HAGB / alpha-beta interface, Burgers variant types) or the IPF colour key; overlays
    hagb / lagb / interface / burgers, prior-beta boundaries (beta_gb) and crack paths."""
    from .settings import load_settings  # noqa: PLC0415

    p = FigureParams.model_validate(ctx.params)
    out_png = ctx.out_dir / f"figure{ctx.figure_ext}"          # .png; .svg / .pdf / .fig for "Save" (fix 3.14)
    if p.kind == "ipf_key":
        style = load_settings().figure
        sheet = ctx.project.sample(p.map_id)
        src = resolve_source(ctx, p.map_id, p.source)
        h = ctx.use(src)
        data = step_npz(ctx, src, "ebsd")
        names = map_phase_names(ctx.project, p.map_id)
        phases = p.phases or [names[k] for k in sorted(names)
                              if (data["phase"] == k).any() and names[k] in (ALPHA, BETA)]
        ph = phases[0]
        s = style.ipf.get(ph) or IpfKeySetting()
        key = p.key or s.key
        vec = _ipf_vector(sheet, p.direction or s.direction, p.custom_vector or s.custom_vector)
        meta = {"kind": p.kind, "map_id": p.map_id, "input_state": sheet.input_state, "warnings": [],
                "keys": {ph: {"key": key, "direction": p.direction or s.direction, "vector": list(vec)}}}
        ctx.backend.render_key(h, ph, key, vec, out_png, style.dpi)
        ctx.output("figure", "png", out_png)
        ctx.output("meta", "json", meta)
        ctx.params.update(source=src)
        return
    spec, handle, meta = figure_spec(ctx, p)
    ctx.backend.render(handle(), spec, out_png)
    ctx.output("figure", "png", out_png)
    ctx.output("meta", "json", meta)


# ---- comparison with OIM images ---------------------------------------------------------------
class CompareOimParams(BaseModel):
    map_id: str
    image: str                                # OIM export (PNG/TIFF/BMP), e.g. data/reference/...
    kind: Literal["ipf", "ci"] = "ipf"
    source: str | None = None                 # map step; use the LOAD step to compare with OIM's own data
    key: str = "ipfTSLKey"                    # OIM-like colours
    direction: Literal["X", "Y", "Z", "build", "custom"] = "Z"
    custom_vector: tuple[float, float, float] | None = None
    phase: str = "Ti (alpha)"


@op("compare.oim", CompareOimParams, uses_backend=True)
def _compare_oim(ctx: OpContext) -> None:
    """Compare our IPF or CI map with an OIM image; scores all 8 flips/rotations so a frame mismatch
    is detected; writes a side-by-side PNG."""
    from .modules.oim_compare import compare  # noqa: PLC0415

    p = CompareOimParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    src = resolve_source(ctx, p.map_id, p.source, ("ebsd.load",) if p.source is None else MAP_SOURCES)
    data = step_npz(ctx, src, "ebsd")
    ctx.add_input(p.image)
    if p.kind == "ipf":
        vec = _ipf_vector(sheet, p.direction, p.custom_vector)
        vals = ctx.backend.ipf_colors(ctx.use(src), p.phase, p.key, vec)
    else:
        vals = data["prop_ci"].astype(float)
    out_png = ctx.out_dir / "comparison.png"
    res = compare(data["x"], data["y"], vals, p.image, p.kind, out_png)
    ctx.params.update(source=src)
    ctx.output("comparison", "png", out_png)
    ctx.output("result", "json", res.model_dump(mode="json"))
