"""EBSD operations of the service layer (phase 2): cleanup, grains, maps, boundaries, figures.

Every op works on a SOURCE map: by default the newest non-stale cleanup of the map, otherwise its
load step; `source` (a step id) overrides that. The resolved source and preset are recorded in the
step params, so replay repeats exactly the same chain.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .backends.base import (BURGERS_TYPES, CleanupParams, GrainParams, MapParams, RenderSpec,
                            cleanup_params_for)
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
PHASE_RGB = {1: (0.53, 0.81, 0.98), 2: (0.56, 0.74, 0.56)}    # MTEX default LightSkyBlue / DarkSeaGreen
BETA_GB_RGB = (0.0, 0.0, 0.0)                                 # prior-beta boundaries: black, 2x width (EBSD_part2)


# ---- grains -----------------------------------------------------------------------------------
class GrainsCalcParams(BaseModel):
    map_id: str
    source: str | None = None
    grains: GrainParams = Field(default_factory=GrainParams)
    boundaries: BoundaryParams = Field(default_factory=BoundaryParams)


@op("grains.calc", GrainsCalcParams, uses_backend=True)
def _grains_calc(ctx: OpContext) -> None:
    """Alpha grain segmentation (HAGB 15°, LAGB 2° inside grains by default) and the boundary
    table with misorientation and nearest Burgers alpha/alpha type per segment."""
    p = GrainsCalcParams.model_validate(ctx.params)
    src = resolve_source(ctx, p.map_id, p.source)
    gh = ctx.backend.calc_grains(ctx.use(src), p.grains)
    ctx.remember(gh)
    gd = ctx.backend.get_grains(gh)
    gb = gd.boundary
    cls = classify_boundaries(gb, p.grains, p.boundaries)
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
    gsrc = resolve_source(ctx, p.map_id, p.grains, ("grains.calc",))
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
FIGURE_KINDS = ("ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd",
                "boundaries", "burgers", "ipf_key")


class FigureParams(BaseModel):
    map_id: str
    kind: Literal["ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd",
                  "boundaries", "burgers", "ipf_key"]
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
    prias_column: int = 0                     # 0 bottom strip, 1 centre square, 2 top strip


def _ipf_vector(sheet, direction: str, custom) -> tuple[float, float, float]:
    if direction == "X":
        return (1.0, 0.0, 0.0)
    if direction == "Y":
        return (0.0, 1.0, 0.0)
    if direction == "Z":
        return (0.0, 0.0, 1.0)
    if direction == "build":
        return sheet.build_direction.unit_vector
    if custom is None:
        raise ValueError("custom IPF direction needs custom_vector")
    return tuple(float(c) for c in custom)


def overlay_segments(ctx: OpContext, map_id: str, overlays: list[str], grains: str | None = None,
                     recon: str | None = None, line_width: float = 1.0) -> dict:
    """Boundary / crack overlays as vector data (same for figures, viewer and exports).
    hagb / lagb / interface / burgers: boundary table of a grains.calc step (newest of the map by default);
    beta_gb: prior-beta grain boundaries of a recon.run step (newest by default); crack: crack paths drawn as
    annotations. Returns seg (x1 y1 x2 y2), rgb (m, 3), widths (m,), legend, per-class lengths and the
    resolved step ids. Segments are in draw order (first = bottom)."""
    from .annotations import load_annotations, rgb_of  # noqa: PLC0415

    parts: list[tuple[dict, np.ndarray, float]] = []          # (seg, rgb (m,3), width)
    legend: list = []
    lengths: dict[str, float] = {}
    used: dict[str, str] = {}
    gb = cls = None
    if any(o in ("hagb", "lagb", "interface", "burgers") for o in overlays):
        gsrc = resolve_source(ctx, map_id, grains, ("grains.calc",))
        gstep = ctx.project.recipe().step(gsrc)
        gp = GrainParams(**gstep.params["grains"])
        bp = BoundaryParams(**gstep.params["boundaries"])
        ctx.step.inputs.append(f"{gsrc}:grains")
        gz = step_npz(ctx, gsrc, "grains")
        gb = {k[3:]: v for k, v in gz.items() if k.startswith("gb_")}
        cls = classify_boundaries(gb, gp, bp)
        used["grains"] = gsrc
    # grain-boundary classes: segments in boundary-table order, a segment in several requested classes
    # takes the colour of the LAST one (as the phase-2 figure op, so recorded figures replay identically)
    gclasses = [o for o in overlays if o in ("hagb", "lagb", "interface", "burgers")]
    if gclasses:
        sel = np.zeros(len(gb["x1"]), bool)
        rgb = np.zeros((len(gb["x1"]), 3))
        for ov in gclasses:
            if ov == "burgers":
                for k, ang in enumerate(BURGERS_TYPES, start=1):
                    m = cls[f"burgers_{k}"]
                    rgb[m], sel = BURGERS_RGB[k - 1], sel | m
                    legend.append((f"{ang:g}°", BURGERS_RGB[k - 1]))
                    lengths[f"{ang:g}°"] = float(gb["length_um"][m].sum())
            else:
                m = cls[ov]
                rgb[m], sel = GB_STYLE[ov][1], sel | m
                legend.append(GB_STYLE[ov])
                lengths[GB_STYLE[ov][0]] = float(gb["length_um"][m].sum())
        parts.append(({k: gb[k][sel] for k in ("x1", "y1", "x2", "y2")}, rgb[sel], line_width))
    for ov in overlays:
        if ov in gclasses:
            continue
        if ov == "beta_gb":
            rid = resolve_source(ctx, map_id, recon, ("recon.run",))
            ctx.step.inputs.append(f"{rid}:recon")
            rz = step_npz(ctx, rid, "recon")
            b = {k[3:]: v for k, v in rz.items() if k.startswith("gb_")}
            m = (b["grain1"] > 0) & (b["grain2"] > 0)
            parts.append(({k: b[k][m] for k in ("x1", "y1", "x2", "y2")}, np.tile(BETA_GB_RGB, (int(m.sum()), 1)),
                          2 * line_width))
            legend.append(("prior β GB", BETA_GB_RGB))
            lengths["prior β GB"] = float(b["length_um"][m].sum())
            used["recon"] = rid
        elif ov == "crack":
            cr = [a for a in load_annotations(ctx.project, map_id) if a.type == "crack"]
            xs = [np.asarray(a.xy, float) for a in cr]
            seg = {"x1": np.concatenate([x[:-1, 0] for x in xs]) if xs else np.zeros(0),
                   "y1": np.concatenate([x[:-1, 1] for x in xs]) if xs else np.zeros(0),
                   "x2": np.concatenate([x[1:, 0] for x in xs]) if xs else np.zeros(0),
                   "y2": np.concatenate([x[1:, 1] for x in xs]) if xs else np.zeros(0)}
            rgb = (np.concatenate([np.tile(rgb_of(a), (len(a.xy) - 1, 1)) for a in cr]) if cr else np.zeros((0, 3)))
            parts.append((seg, rgb, 2 * line_width))
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
    names = {1: "Ti (alpha)", 2: "Ti (beta)"}
    spec = RenderSpec(width_cm=style.width_cm, dpi=style.dpi, scale_bar=style.scale_bar,
                      seg_width=style.boundary_line_width)
    meta: dict[str, Any] = {"kind": p.kind, "map_id": p.map_id, "input_state": sheet.input_state, "warnings": []}

    if p.kind == "ipf":
        phases = p.phases or [names[k] for k in sorted(names) if (phase == k).any()]
        rgb = np.full((phase.size, 3), np.nan)
        keys = {}
        for ph in phases:
            s = (p.ipf_keys or {}).get(ph) or style.ipf.get(ph) or IpfKeySetting()
            key = p.key or s.key
            vec = _ipf_vector(sheet, p.direction or s.direction, p.custom_vector or s.custom_vector)
            keys[ph] = {"key": key, "direction": p.direction or s.direction, "vector": list(vec)}
            c = ctx.backend.ipf_colors(handle(), ph, key, vec)
            m = ~np.isnan(c[:, 0])
            rgb[m] = c[m]
        meta["keys"] = keys
        spec.rgb = rgb
        spec.title = "IPF " + ", ".join(f"{k}: {v['key']} ∥ {v['direction']}" for k, v in keys.items())
    elif p.kind == "phase":
        rgb = np.full((phase.size, 3), np.nan)
        for k, c in PHASE_RGB.items():
            rgb[phase == k] = c
        spec.rgb = rgb
        spec.legend = [(names[k], PHASE_RGB[k]) for k in sorted(PHASE_RGB) if (phase == k).any()]
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
    elif p.kind == "prias":
        from .io.ang import read_prias  # noqa: PLC0415

        spec.values = read_prias(sheet.map_file, p.prias_column)
        spec.cmap, spec.colorbar_label, spec.title = "gray", "PRIAS", "PRIAS (virtual BSE)"
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
    out_png = ctx.out_dir / "figure.png"
    if p.kind == "ipf_key":
        style = load_settings().figure
        sheet = ctx.project.sample(p.map_id)
        src = resolve_source(ctx, p.map_id, p.source)
        h = ctx.use(src)
        data = step_npz(ctx, src, "ebsd")
        names = {1: "Ti (alpha)", 2: "Ti (beta)"}
        phases = p.phases or [names[k] for k in sorted(names) if (data["phase"] == k).any()]
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
