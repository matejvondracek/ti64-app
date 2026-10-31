"""Prior-beta reconstruction operations of the service layer (phase 3).

recon.run     reconstruct one map (EBSD_part2.m port, both backends), parent map + parent grains +
              Ti variant / packet IDs + variant-selection and grain-shape statistics
recon.figure  the EBSD_part2.m figures, drawn by the backend that computed the reconstruction
recon.sweep   reconstruction-parameter sweep: numbers + thumbnails per setting

Input by default = the LOADED map (newest ebsd.load) with EBSD_part2's own noise cleanup (D12).
"""
from __future__ import annotations

import itertools
import time
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .backends.base import ChartSpec, RECON_TRANSFORMED, ReconData, ReconParams, RenderSpec
from .modules.recon_analysis import grain_mean_parent, grain_shape, ti_variants, variant_stats
from .ops_ebsd import _ipf_vector, resolve_source, step_npz
from .service import OpContext, op
from .settings import IpfKeySetting

RECON_SOURCES = ("ebsd.load", "ebsd.cleanup")
BETA, ALPHA = "Ti (beta)", "Ti (alpha)"


def _json(v: Any) -> Any:
    """numpy -> plain Python for JSON outputs."""
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, np.ndarray):
        return _json(v.tolist())
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


# ---- recon.run ----------------------------------------------------------------------------------
class ReconRunParams(BaseModel):
    map_id: str
    source: str | None = None                 # load (default: newest ebsd.load of the map) or cleanup step
    params: ReconParams = Field(default_factory=ReconParams)
    min_px: int = 1                           # grain statistics: minimum pixels per grain (ISO 13067)
    variant_min_share: float = 0.01           # a variant counts as present in a grain above this share
    write_log: bool = True                    # append the EBSD_part2 run-log line to <project>/reconstruction_log.txt


SUMMARY_KEYS = ("or_dev_deg", "or_used", "n_noise_px", "n_child_grains", "n_grains_min_ecd", "n_grains_all",
                "rec_frac", "mean_fit_deg", "bad_fit_frac", "ecd_aw_um", "bridged_pct", "split_pct",
                "fit_quantiles", "mis_share", "sweep")


@op("recon.run", ReconRunParams, uses_backend=True)
def _recon_run(ctx: OpContext) -> None:
    """Prior-beta reconstruction (EBSD_part2.m: noise cleanup, alpha grains 1.5° + smoothing, Burgers OR
    with refinement and the visible 1° rule, variant graph, vote, merge, inclusions, merge sweep).
    Outputs: parent map, parent grains + boundaries, Ti variant / packet IDs (12 ideal Burgers variants),
    variant-selection statistics, prior-beta grain size and caliper along / across the build direction."""
    p = ReconRunParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    src = resolve_source(ctx, p.map_id, p.source, RECON_SOURCES)
    rebuilding = ctx.out_dir.parent != ctx.project.outputs_dir       # materialize(): no second log line
    log = str(ctx.project.root / "reconstruction_log.txt") if p.write_log and not rebuilding else ""
    t0 = time.perf_counter()
    rh, res = ctx.backend.reconstruct(ctx.use(src), p.params, log)
    t1 = time.perf_counter()
    ctx.remember(rh)
    pmap = ctx.backend.get_ebsd(rh)
    rd = ctx.backend.get_recon(rh)
    child = step_npz(ctx, src, "ebsd")

    st = pmap.props["recon_state"].astype(np.int8)
    pg = pmap.props["parent_grain"].astype(np.int64)
    fit = pmap.props["recon_fit"].astype(float)
    cq = np.where((st == RECON_TRANSFORMED)[:, None], child["quat"], np.nan)
    var = ti_variants(cq, grain_mean_parent(pg, rd.grain))
    vstats = variant_stats(pg, var["variant"], var["packet"], fit, p.variant_min_share)
    shape = grain_shape(pmap.x, pmap.y, pg, rd.grain, sheet.build_direction.unit_vector, pmap.step_um,
                        p.min_px, p.params.histMinECD)
    ok = ~np.isnan(var["c_dev_deg"])
    summary = {k: res[k] for k in SUMMARY_KEYS if k in res}
    summary.update(
        backend=ctx.backend.name, map_id=p.map_id, input_state=sheet.input_state, source=src,
        or_rule=f"orMode {p.params.orMode}, refined OR used only if <= {p.params.orMaxDev:g}° from Burgers"
                if p.params.orMode == "limit" else f"orMode {p.params.orMode}",
        sweep_configs=[list(c) for c in p.params.sweep],
        sweep_columns=["grains >= histMinECD", "ECD area-weighted (um)", "bridged steps (%)", "split noise (%)"],
        state_counts={n: int((st == c).sum()) for c, n in enumerate(
            ("not_indexed", "removed_noise", "reconstructed_alpha", "untransformed_alpha", "retained_beta"))},
        variants={"numbering": "12 ideal Burgers variants: packet = {110}beta || (0001)alpha (1..6), "
                               "variant = 2 (packet - 1) + j (decisions D13)",
                  "c_dev_deg_p50_p90": np.percentile(var["c_dev_deg"][ok], [50, 90]).tolist() if ok.any() else None,
                  "a_dev_deg_p50_p90": np.percentile(var["a_dev_deg"][ok], [50, 90]).tolist() if ok.any() else None,
                  **{k: v for k, v in vstats.items() if k != "grains"}},
        grain_shape=shape["summary"])
    ctx.params.update(source=src)
    ctx.metrics.update(recon_s=round(t1 - t0, 2), n_parent_grains=int(len(rd.grain["id"])),
                       rec_frac=round(float(res["rec_frac"]), 4))
    ctx.output("ebsd", "npz", pmap)
    ctx.output("recon", "npz", rd.to_arrays())
    ctx.output("variants", "npz", {k: v for k, v in var.items()})
    ctx.output("grain_table", "npz", shape["table"])
    ctx.output("variant_grains", "json", _json(vstats["grains"]))
    ctx.output("summary", "json", _json(summary))


# ---- recon.figure -------------------------------------------------------------------------------
RECON_FIGURES = ("parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                 "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist")


class ReconFigureParams(BaseModel):
    map_id: str
    kind: Literal["parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                  "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist"]
    recon: str | None = None                  # recon.run step (default: newest of the map)
    key: str | None = None                    # IPF key override (default: Settings per phase)
    direction: Literal["X", "Y", "Z", "build", "custom"] | None = None
    custom_vector: tuple[float, float, float] | None = None
    clim: tuple[float, float] | None = None
    width_cm: float | None = None             # default: Settings
    dpi: int | None = None


def _ipf_choice(p, sheet, style, phase):
    s = style.ipf.get(phase) or IpfKeySetting()
    key = p.key or s.key
    d = p.direction or s.direction
    return key, d, _ipf_vector(sheet, d, p.custom_vector or s.custom_vector)


def _segs(b: dict, m: np.ndarray) -> dict:
    return {k: b[k][m] for k in ("x1", "y1", "x2", "y2")}


def discrete_hsv(n: int) -> np.ndarray:
    """MATLAB hsv(n): hue k/n, full saturation and value (EBSD_part2 variant map), same in both backends."""
    import colorsys  # noqa: PLC0415

    return np.array([colorsys.hsv_to_rgb(k / n, 1.0, 1.0) for k in range(n)])


def _turbo(v: np.ndarray, lo: float, hi: float) -> np.ndarray:
    from matplotlib import colormaps  # noqa: PLC0415

    return colormaps["turbo"](np.clip((v - lo) / (hi - lo), 0, 1))[:, :3]


def recon_figure(ctx: OpContext, p: ReconFigureParams, rstep_id: str, out_png) -> dict:
    """Draw one reconstruction figure with ctx.backend; returns the figure meta."""
    from .settings import load_settings  # noqa: PLC0415

    sheet = ctx.project.sample(p.map_id)
    style = load_settings().figure
    rstep = ctx.project.recipe().step(rstep_id)
    if rstep.backend != ctx.backend.name:
        raise ValueError(f"reconstruction {rstep_id} was computed by {rstep.backend}; a figure always comes "
                         f"from the engine that computed it: use backend={rstep.backend!r} or reconstruct again")
    rp = ReconParams(**rstep.params["params"])
    pmap = step_npz(ctx, rstep_id, "ebsd")
    rd = ReconData.from_arrays(step_npz(ctx, rstep_id, "recon"))
    summary = ctx.project.root / rstep.outputs["summary"].path
    rh = ctx.use(rstep_id)
    width, dpi = p.width_cm or style.width_cm, p.dpi or style.dpi
    lw = style.boundary_line_width
    meta: dict[str, Any] = {"kind": p.kind, "map_id": p.map_id, "recon": rstep_id, "backend": ctx.backend.name}
    b = rd.boundary
    inner = (b["grain1"] > 0) & (b["grain2"] > 0)
    spec = RenderSpec(width_cm=width, dpi=dpi, scale_bar=style.scale_bar, seg_width=lw)
    pg = pmap["prop_parent_grain"].astype(np.int64)
    n = pg.size

    def chart(c: ChartSpec):
        c.width_cm, c.dpi = width, dpi
        ctx.backend.render_chart(c, out_png)
        return meta

    if p.kind == "parent_ipf":
        key, d, vec = _ipf_choice(p, sheet, style, BETA)
        gcol = ctx.backend.ipf_colors_quat(rh, BETA, rd.grain["mean_quat"], key, vec)
        lut = np.full(max(int(pg.max(initial=0)), int(rd.grain["id"].max(initial=0))) + 1, -1)
        lut[rd.grain["id"]] = np.arange(rd.grain["id"].size)
        rgb = np.full((n, 3), np.nan)
        ok = (pg > 0) & (lut[pg] >= 0)
        rgb[ok] = gcol[lut[pg[ok]]]
        hagb = inner & (np.nan_to_num(b["mis_deg"], nan=0) > rp.hagbAngle)
        spec.rgb, spec.seg, spec.seg_rgb = rgb, _segs(b, hagb), np.zeros((int(hagb.sum()), 3))
        spec.title = f"prior β grains, mean orientation, {key} ∥ {d}; boundaries > {rp.hagbAngle:g}°"
        meta.update(key=key, direction=d, vector=list(vec))
    elif p.kind == "alpha_parent_gb":
        key, d, vec = _ipf_choice(p, sheet, style, ALPHA)
        src = rstep.params["source"]
        rgb = ctx.backend.ipf_colors(ctx.use(src), ALPHA, key, vec)
        ctx.backend.render(ctx.use(src), RenderSpec(
            rgb=rgb, seg=_segs(b, inner), seg_rgb=np.zeros((int(inner.sum()), 3)), seg_width=lw,
            title=f"α {key} ∥ {d} with prior β boundaries", width_cm=width, dpi=dpi, scale_bar=style.scale_bar),
            out_png)
        meta.update(key=key, direction=d, vector=list(vec))
        return meta
    elif p.kind in ("fit", "variant", "packet"):
        if p.kind == "fit":
            spec.values, spec.cmap, spec.clim = pmap["prop_recon_fit"], style.colormap_scalar, p.clim or (0.0, 5.0)
            spec.colorbar_label = "fit to Burgers OR (°)"
            spec.title = spec.colorbar_label
        else:
            v = step_npz(ctx, rstep_id, "variants")[p.kind].astype(np.int64)
            cols = discrete_hsv(12 if p.kind == "variant" else 6)
            rgb = np.full((n, 3), np.nan)
            rgb[v > 0] = cols[v[v > 0] - 1]
            spec.rgb = rgb
            spec.legend = [(str(k + 1), tuple(float(c) for c in cols[k])) for k in range(len(cols))]
            spec.title = f"{p.kind} ID (ideal Burgers, per prior β grain)"
        spec.seg, spec.seg_rgb = _segs(b, inner), np.ones((int(inner.sum()), 3))
    elif p.kind == "mis_map":
        pre = rd.pre_boundary
        sel = pre["mis_deg"] >= rp.misMapMin
        segs = {k: np.concatenate([b[k][inner], pre[k][sel]]) for k in ("x1", "y1", "x2", "y2")}
        cols = np.concatenate([np.full((int(inner.sum()), 3), 0.75), _turbo(pre["mis_deg"][sel], rp.misMapMin,
                                                                          rp.misMapMax)])
        spec.rgb, spec.seg, spec.seg_rgb = np.full((n, 3), np.nan), segs, cols
        spec.seg_widths = np.r_[np.full(int(inner.sum()), 3 * lw), np.full(int(sel.sum()), lw)]   # EBSD_part2
        ticks = np.linspace(rp.misMapMin, rp.misMapMax, 5)
        spec.legend = [("final prior β boundaries", (0.75, 0.75, 0.75))] + [
            (f"{t:.1f}°", tuple(float(c) for c in _turbo(np.array([t]), rp.misMapMin, rp.misMapMax)[0])) for t in ticks]
        spec.title = "β misorientation across α-grain boundaries before merging"
    elif p.kind == "parent_key":
        key, d, vec = _ipf_choice(p, sheet, style, BETA)
        ctx.backend.render_key(rh, BETA, key, vec, out_png, dpi)
        meta.update(key=key, direction=d, vector=list(vec))
        return meta
    elif p.kind == "mis_hist":
        pre = rd.pre_boundary
        edges = np.arange(0, 15.0001, 0.25)
        h, _ = np.histogram(pre["mis_deg"], edges, weights=pre["length_um"])
        h = 100 * h / pre["length_um"].sum()
        meta["share_pct"] = h.tolist()
        return chart(ChartSpec(edges[:-1] + 0.125, np.where(h > 0, h, np.nan), 0.25,
                               "misorientation between β of neighbouring α grains (°)",
                               "share of boundary length (%)", "", True, [(rp.mergeThreshold, "merge threshold")]))
    elif p.kind == "fit_hist":
        f = pmap["prop_recon_fit"]
        f = f[~np.isnan(f)]
        edges = np.arange(0, 10.0001, 0.25)
        h, _ = np.histogram(np.clip(f, 0, 10), edges)
        return chart(ChartSpec(edges[:-1] + 0.125, 100 * h / max(f.size, 1), 0.25, "fit to Burgers OR (°)",
                               "share of reconstructed α pixels (%)", "", False, [(rp.badFit, "bad fit")]))
    elif p.kind == "ecd_hist":
        e = rd.grain["ecd_um"]
        e = e[e >= rp.histMinECD]
        edges = np.histogram_bin_edges(e, "auto") if e.size > 1 else np.array([0, 1.0])
        h, _ = np.histogram(e, edges)
        meta.update(n=int(e.size), min_ecd_um=rp.histMinECD, edge_grains="included (as EBSD_part2.m)")
        return chart(ChartSpec((edges[:-1] + edges[1:]) / 2, h, float(np.diff(edges).min()),
                               "prior β grain diameter ECD (µm)", "count",
                               f"prior β grains, ECD ≥ {rp.histMinECD:g} µm (n = {e.size})"))
    elif p.kind == "n_variants_hist":
        import json  # noqa: PLC0415

        s = json.loads(summary.read_text(encoding="utf-8"))["variants"]
        h = np.asarray(s["n_variants_hist"], float)[1:]
        return chart(ChartSpec(np.arange(1, 13.0), h, 0.8, "number of α variants in a prior β grain", "grains",
                               f"variants above {100 * s['min_share']:g} % of the grain"))
    ctx.backend.render(rh, spec, out_png)
    return meta


@op("recon.figure", ReconFigureParams, uses_backend=True)
def _recon_figure(ctx: OpContext) -> None:
    """Figures of a reconstruction (EBSD_part2.m): parent grain IPF map (grain mean orientation, beta key,
    boundaries > hagbAngle), alpha IPF with prior-beta boundaries, fit map, variant / packet maps, boundary
    misorientation map, beta IPF key, histograms (neighbour misorientation, fit, ECD, variants per grain).
    Drawn by the backend that computed the reconstruction."""
    p = ReconFigureParams.model_validate(ctx.params)
    rid = resolve_source(ctx, p.map_id, p.recon, ("recon.run",))
    out_png = ctx.out_dir / "figure.png"
    meta = recon_figure(ctx, p, rid, out_png)
    ctx.params.update(recon=rid)
    ctx.output("figure", "png", out_png)
    ctx.output("meta", "json", _json(meta))


# ---- recon.sweep --------------------------------------------------------------------------------
class ReconSweepParams(BaseModel):
    map_id: str
    source: str | None = None
    base: ReconParams = Field(default_factory=lambda: ReconParams(sweep=[]))
    grid: dict[str, list[Any]] = Field(default_factory=lambda: {"vgThreshold": [5.0, 8.0],
                                                                "mergeThreshold": [0.5, 3.0, 6.0]})
    thumbnails: bool = True
    thumb_width_cm: float = 6.0
    thumb_dpi: int = 100
    max_runs: int = 24


@op("recon.sweep", ReconSweepParams, uses_backend=True)
def _recon_sweep(ctx: OpContext) -> None:
    """Reconstruction-parameter sweep: every combination of `grid` (ReconParams fields) on top of `base`;
    per run reconstructed fraction, parent-fit quantiles, beta grain count, area-weighted ECD, bridged
    steps, split noise, OR used, and a parent-grain IPF thumbnail (drawn by the active backend)."""
    from .settings import load_settings  # noqa: PLC0415

    p = ReconSweepParams.model_validate(ctx.params)
    unknown = set(p.grid) - set(ReconParams.model_fields)
    if unknown:
        raise ValueError(f"unknown reconstruction parameters: {sorted(unknown)}")
    combos = list(itertools.product(*p.grid.values()))
    if len(combos) > p.max_runs:
        raise ValueError(f"{len(combos)} runs > max_runs = {p.max_runs}")
    sheet = ctx.project.sample(p.map_id)
    style = load_settings().figure
    src = resolve_source(ctx, p.map_id, p.source, RECON_SOURCES)
    h = ctx.use(src)
    rows = []
    key, d, vec = _ipf_choice(ReconFigureParams(map_id=p.map_id, kind="parent_ipf"), sheet, style, BETA)
    for i, combo in enumerate(combos):
        rp = ReconParams(**{**p.base.model_dump(), **dict(zip(p.grid, combo))})
        t0 = time.perf_counter()
        rh, res = ctx.backend.reconstruct(h, rp, "")
        row = {"run": i, "params": dict(zip(p.grid, combo)), **{k: res[k] for k in (
            "or_dev_deg", "or_used", "n_grains_min_ecd", "n_grains_all", "rec_frac", "mean_fit_deg", "bad_fit_frac",
            "ecd_aw_um", "bridged_pct", "split_pct", "fit_quantiles")}}
        ctx.metrics[f"run{i:02d}_s"] = round(time.perf_counter() - t0, 1)
        if p.thumbnails:
            rd = ctx.backend.get_recon(rh)
            pm = ctx.backend.get_ebsd(rh)
            pg = pm.props["parent_grain"].astype(np.int64)
            gcol = ctx.backend.ipf_colors_quat(rh, BETA, rd.grain["mean_quat"], key, vec)
            lut = np.full(max(int(pg.max(initial=0)), int(rd.grain["id"].max(initial=0))) + 1, -1)
            lut[rd.grain["id"]] = np.arange(rd.grain["id"].size)
            rgb = np.full((pg.size, 3), np.nan)
            ok = (pg > 0) & (lut[pg] >= 0)
            rgb[ok] = gcol[lut[pg[ok]]]
            label = ", ".join(f"{k} {v}" for k, v in row["params"].items())
            png = ctx.out_dir / f"thumb_{i:02d}.png"
            ctx.backend.render(rh, RenderSpec(rgb=rgb, title=f"{label}: {res['n_grains_min_ecd']} grains",
                                              width_cm=p.thumb_width_cm, dpi=p.thumb_dpi, scale_bar=True), png)
            ctx.output(f"thumb_{i:02d}", "png", png)
        ctx.backend.release(rh)
        rows.append(_json(row))
    ctx.params.update(source=src)
    ctx.output("table", "json", {"base": p.base.model_dump(mode="json"), "grid": p.grid,
                                 "ipf": {"key": key, "direction": d}, "runs": rows})
