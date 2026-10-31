"""Grain-size operations of the service layer (phase 4; ISO 13067 / ASTM E2627 rules, docs/decisions.md D14).

grainsize.run          alpha grain (lath) ECD number- and area-weighted, lath thickness (minor axis of the
                       second-moment ellipse + linear intercept perpendicular to each lath's long axis),
                       colony / packet size (laths merged across Burgers 10.53 deg boundaries, MTEX merge in
                       the backend), prior-beta ECD + width / length relative to the build direction (mean
                       intercepts across / along BD + per-grain calipers), retained-beta fraction;
                       log-normal fits; tables as CSV + one XLSX workbook per map
grainsize.figure       histograms with log-normal fits and the colony map, drawn by the backend that computed
                       the grain-size step
grainsize.compare_oim  validation against an OIM Analysis grain file export (same rules applied)

Backend parts (both engines): grain segmentation (grains.calc), MTEX perimeter / calipers (grain_geometry),
colony merge (merge_colonies). Python-only (modules/grain_size.py, same for both): second-moment ellipses,
line intercepts, statistics, tables.
"""
from __future__ import annotations

import colorsys
import json
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .backends.base import ChartSpec, ColonyParams, GrainParams, ReconData, ReconParams, RenderSpec
from .modules.grain_size import (GridIndex, directional_intercepts, dist_stats, hist_edges, lath_intercepts,
                                 lognormal_pdf, moment_ellipse, per_label_mean, read_oim_grain_file)
from .modules.recon_analysis import bd_in_plane, grain_shape
from .ops_ebsd import resolve_source, step_npz
from .ops_recon import _json
from .service import OpContext, latest_step, op

RESOLUTION_NOTE = ("EBSD resolves only features of a few steps: laths thinner than ~3 steps and beta ribs between "
                   "laths are under-resolved, so lath thickness is an upper estimate and the retained-beta "
                   "fraction a lower bound")


def grid_info(x: np.ndarray, y: np.ndarray) -> tuple[float, str]:
    """(step_um, 'hex' | 'square') of a map from its pixel centres."""
    yu = np.unique(np.round(y, 5))
    dy = float(np.median(np.diff(yu))) if yu.size > 1 else 1.0
    row = x[np.isclose(y, yu[0])]
    step = float(np.median(np.diff(np.unique(np.round(row, 5))))) if row.size > 1 else dy
    return round(step, 6), ("hex" if abs(dy / step - np.sqrt(3) / 2) < 0.05 else "square")


def _phase_ids(ctx: OpContext, map_id: str) -> dict[str, int]:
    load = latest_step(ctx.project, ("ebsd.load",), map_id=map_id)
    names = json.loads((ctx.project.root / load.outputs["meta"].path).read_text(encoding="utf-8"))["phase_names"]
    out = {}
    for k, v in names.items():
        if "alpha" in v:
            out["alpha"] = int(k)
        elif "beta" in v:
            out["beta"] = int(k)
    return out


# ---- grainsize.run ------------------------------------------------------------------------------
class GrainSizeParams(BaseModel):
    map_id: str
    grains: str | None = None                 # grains.calc step = lath partition (threshold set there, default 15°)
    recon: str | None = None                  # recon.run step for prior beta (default newest of the map, if any)
    use_recon: bool = True                    # False: no prior-beta part even if a reconstruction exists
    min_px: int = 5                           # grains with fewer pixels excluded (ISO 13067 / E2627 rule; D14)
    exclude_edge: bool = True                 # grains touching the map edge excluded from per-grain statistics
    lath_min_aspect: float = 2.0              # a used alpha grain is a LATH if major / minor axis >= this
    lath_angle_bin_deg: float = 5.0           # laths grouped by long-axis angle for the intercept lines
    lath_line_spacing_um: float | None = None   # default: the map step
    sample_um: float | None = None            # sampling along test lines (default: step / 4)
    colony: ColonyParams = Field(default_factory=ColonyParams)
    beta_line_spacing_um: float = 1.0         # test-line spacing for prior-beta intercepts
    beta_min_ecd_um: float | None = None      # prior-beta per-grain statistics (default: recon histMinECD)
    qa_min_mean_px: int = 100                 # warn if the mean used grain has fewer pixels (D14, to verify)
    qa_min_grains: int = 100                  # warn if fewer grains are used


STAT_COLS = ("n", "mean", "sd", "median", "p10", "p90")


def _row(quantity: str, unit: str, method: str, s: dict | None, **extra) -> dict:
    r = {"quantity": quantity, "unit": unit, "method": method}
    for k in STAT_COLS:
        r[k] = s.get(k) if s else None
    ln = (s or {}).get("lognormal", {})
    r.update(lognormal_mu=ln.get("mu"), lognormal_sigma=ln.get("sigma"), lognormal_median=ln.get("median"),
             ks_D=ln.get("ks_D"), ks_p=ln.get("ks_p"))
    aw = (s or {}).get("area_weighted")
    r.update(area_weighted_mean=aw["mean"] if aw else None,
             aw_lognormal_mu=aw["lognormal"]["mu"] if aw else None,
             aw_lognormal_sigma=aw["lognormal"]["sigma"] if aw else None)
    r.update(extra)
    return r


def _rows_to_table(rows: list[dict]) -> dict[str, np.ndarray]:
    keys = list(rows[0])
    for r in rows[1:]:
        keys += [k for k in r if k not in keys]
    return {k: np.array([np.nan if r.get(k) is None else r.get(k) for r in rows], dtype=object) for k in keys}


def _rules(p: GrainSizeParams) -> str:
    return (f"grains < {p.min_px} px excluded; " + ("edge grains excluded" if p.exclude_edge else "edge grains kept")
            + " (ISO 13067 / ASTM E2627)")


@op("grainsize.run", GrainSizeParams, uses_backend=True)
def _grainsize_run(ctx: OpContext) -> None:
    """Grain size per ISO 13067 / ASTM E2627 rules (min pixels, edge grains excluded): alpha grain ECD (number-
    and area-weighted), lath thickness (minor axis of the second-moment ellipse + median linear intercept
    perpendicular to the lath long axis), colony / packet size (laths merged across Burgers 10.53° boundaries),
    prior-beta ECD and width / length relative to the build direction (mean intercepts across / along BD, E112
    half-counting at the map edge, + per-grain calipers), retained-beta fraction. Log-normal fits; CSV tables
    and an XLSX workbook per map."""
    p = GrainSizeParams.model_validate(ctx.params)
    sheet = ctx.project.sample(p.map_id)
    gsrc = resolve_source(ctx, p.map_id, p.grains, ("grains.calc",))
    gstep = ctx.project.recipe().step(gsrc)
    gp = GrainParams(**gstep.params["grains"])
    gh = ctx.use(gsrc, "grains")
    gz = step_npz(ctx, gsrc, "grains")
    msrc = gstep.params["source"]
    data = step_npz(ctx, msrc, "ebsd")
    x, y, phase = data["x"], data["y"], data["phase"]
    step, grid = grid_info(x, y)
    sample = p.sample_um or step / 4
    pid = _phase_ids(ctx, p.map_id)
    alpha, beta = pid.get("alpha", 1), pid.get("beta", 2)
    warnings: list[str] = []
    gi = GridIndex(x, y, step)

    # -- alpha grains (laths) ---------------------------------------------------------------------
    g = {k[6:]: v for k, v in gz.items() if k.startswith("grain_")}
    pix = gz["pixel_grain_id"].astype(np.int64)
    ids = g["id"].astype(np.int64)
    geo = ctx.backend.grain_geometry(gh)
    order = np.argsort(geo["id"])
    pos = np.searchsorted(geo["id"][order], ids)
    if not np.array_equal(geo["id"][order][pos], ids):
        raise RuntimeError("grain_geometry ids do not match the grains table")
    geo = {k: v[order][pos] for k, v in geo.items()}
    ell = moment_ellipse(x, y, pix, ids, step, grid)
    is_alpha = g["phase"] == alpha
    used = is_alpha & (g["n_px"] >= p.min_px) & (~g["is_edge"].astype(bool) if p.exclude_edge else True)
    aspect = np.divide(ell["major_um"], ell["minor_um"], out=np.full(ids.size, np.inf), where=ell["minor_um"] > 0)
    is_lath = used & (aspect >= p.lath_min_aspect)
    li = lath_intercepts(gi, pix, ids[is_lath], ell["angle_deg"][is_lath], p.lath_line_spacing_um or step, sample,
                         p.lath_angle_bin_deg)
    thick = np.full(ids.size, np.nan)
    nchord = np.zeros(ids.size, np.int64)
    thick[is_lath], nchord[is_lath] = li["median_chord_um"], li["n_chords"]
    lath_table = {"id": ids, "phase": g["phase"], "n_px": g["n_px"], "area_um2": g["area_um2"], "ecd_um": g["ecd_um"],
                  "is_edge": g["is_edge"].astype(bool), "used": used, "is_lath": is_lath,
                  "perimeter_um": geo["perimeter_um"], "caliper_long_um": geo["caliper_long_um"],
                  "caliper_short_um": geo["caliper_short_um"], "caliper_long_angle_deg": geo["caliper_long_angle_deg"],
                  "ellipse_major_um": ell["major_um"], "ellipse_minor_um": ell["minor_um"],
                  "ellipse_angle_deg": ell["angle_deg"], "aspect": aspect, "centroid_x_um": ell["cx_um"],
                  "centroid_y_um": ell["cy_um"], "thickness_intercept_um": thick, "n_chords": nchord}
    s_alpha = dist_stats(g["ecd_um"][used], g["area_um2"][used])
    s_thick = dist_stats(thick[is_lath])
    s_minor = dist_stats(ell["minor_um"][is_lath])
    s_chord = dist_stats(li["chord_um"])
    n_used = int(used.sum())
    mean_px = float(g["n_px"][used].mean()) if n_used else 0.0
    if n_used < p.qa_min_grains:
        warnings.append(f"only {n_used} alpha grains used (< {p.qa_min_grains})")
    if mean_px < p.qa_min_mean_px:
        warnings.append(f"alpha grains have {mean_px:.0f} px on average (< {p.qa_min_mean_px}): the step "
                        f"({step:g} um) is coarse for these grains; size statistics carry pixelation error")

    # -- colonies -----------------------------------------------------------------------------------
    ch = ctx.backend.merge_colonies(gh, p.colony)
    cd = ctx.backend.get_grains(ch)
    cgeo = ctx.backend.grain_geometry(ch)
    ctx.backend.release(ch)
    cg = cd.grain
    cids = cg["id"].astype(np.int64)
    co = np.argsort(cgeo["id"])
    cgeo = {k: v[co][np.searchsorted(cgeo["id"][co], cids)] for k, v in cgeo.items()}
    cpix = cd.pixel_grain_id.astype(np.int64)
    cell = moment_ellipse(x, y, cpix, cids, step, grid)
    # lath -> colony (majority of the lath's indexed pixels; equal for all pixels of a lath by construction)
    m = (pix > 0) & (cpix > 0)
    lath_colony = np.zeros(int(max(pix.max(initial=0), ids.max(initial=0))) + 1, np.int64)
    lath_colony[pix[m]] = cpix[m]
    alpha_ids = ids[is_alpha]
    n_laths = np.bincount(lath_colony[alpha_ids], minlength=int(cids.max(initial=0)) + 1)[cids]
    c_alpha = cg["phase"] == alpha
    c_used = c_alpha & (cg["n_px"] >= p.min_px) & (~cg["is_edge"].astype(bool) if p.exclude_edge else True)
    colony_table = {"id": cids, "n_px": cg["n_px"], "area_um2": cg["area_um2"], "ecd_um": cg["ecd_um"],
                    "is_edge": cg["is_edge"].astype(bool), "used": c_used, "n_alpha_grains": n_laths,
                    "perimeter_um": cgeo["perimeter_um"], "caliper_long_um": cgeo["caliper_long_um"],
                    "caliper_short_um": cgeo["caliper_short_um"], "ellipse_major_um": cell["major_um"],
                    "ellipse_minor_um": cell["minor_um"], "ellipse_angle_deg": cell["angle_deg"]}
    s_colony = dist_stats(cg["ecd_um"][c_used], cg["area_um2"][c_used])
    n_merged = int(is_alpha.sum() - c_alpha.sum())
    if n_merged == 0 and gp.hagb_deg >= 10.53 - p.colony.burgers_tol_deg:
        warnings.append(f"alpha grains are bounded at {gp.hagb_deg:g}° >= 10.53° - {p.colony.burgers_tol_deg:g}°: "
                        "laths of one packet (10.53° about <0001>) are already inside one grain, so colonies = alpha "
                        "grains; segment laths with a lower grains.calc threshold (e.g. 5°) to separate them")

    # -- retained beta ------------------------------------------------------------------------------
    load = latest_step(ctx.project, ("ebsd.load",), map_id=p.map_id)
    lphase = step_npz(ctx, load.id, "ebsd")["phase"]
    rb = {"loaded": {"beta_px": int((lphase == beta).sum()), "indexed_px": int((lphase > 0).sum())},
          "source_map": {"step": msrc, "beta_px": int((phase == beta).sum()), "indexed_px": int((phase > 0).sum())},
          "n_beta_grains": int((g["phase"] == beta).sum()), "note": RESOLUTION_NOTE}
    for k in ("loaded", "source_map"):
        rb[k]["fraction"] = rb[k]["beta_px"] / max(rb[k]["indexed_px"], 1)

    # -- prior beta -----------------------------------------------------------------------------------
    rid = None
    if p.use_recon:
        rid = p.recon or getattr(latest_step(ctx.project, ("recon.run",), map_id=p.map_id), "id", None)
    beta_part: dict[str, Any] | None = None
    beta_table: dict[str, np.ndarray] | None = None
    beta_chords: dict[str, np.ndarray] = {}
    if rid:
        rstep = ctx.project.recipe().step(rid)
        ctx.step.inputs.append(f"{rid}:recon")
        rp = ReconParams(**rstep.params["params"])
        pm = step_npz(ctx, rid, "ebsd")
        rd = ReconData.from_arrays(step_npz(ctx, rid, "recon"))
        pg = pm["prop_parent_grain"].astype(np.int64)
        bx, by = pm["x"], pm["y"]
        bgi = gi if (bx.size == x.size and np.array_equal(bx, x) and np.array_equal(by, y)) else GridIndex(bx, by, step)
        min_ecd = p.beta_min_ecd_um if p.beta_min_ecd_um is not None else rp.histMinECD
        shape = grain_shape(bx, by, pg, rd.grain, sheet.build_direction.unit_vector, step, p.min_px, min_ecd)
        st = pm["prop_recon_state"].astype(int)
        rb["reconstruction"] = {"step": rid, "retained_beta_px": int((st == 4).sum()),
                                "fraction_of_kept": float((st == 4).sum() / max(int((st >= 2).sum()), 1))}
        u, out_deg = bd_in_plane(sheet.build_direction.unit_vector)
        rg = rd.grain
        bused = ((rg["n_px"] >= p.min_px) & (rg["ecd_um"] >= min_ecd)
                 & (~rg["is_edge"].astype(bool) if p.exclude_edge else True))
        beta_table = {"id": rg["id"].astype(np.int64), "n_px": rg["n_px"], "area_um2": rg["area_um2"],
                      "ecd_um": rg["ecd_um"], "is_edge": rg["is_edge"].astype(bool), "used": bused,
                      "caliper_along_bd_um": shape["table"]["along_bd_um"],
                      "caliper_across_bd_um": shape["table"]["across_bd_um"], "aspect": shape["table"]["aspect"]}
        beta_part = {"recon": rid, "recon_backend": rstep.backend, "or_used": json.loads(
            (ctx.project.root / rstep.outputs["summary"].path).read_text(encoding="utf-8")).get("or_used"),
            "min_ecd_um": min_ecd, "ecd": dist_stats(rg["ecd_um"][bused], rg["area_um2"][bused]),
            "caliper": shape["summary"], "bd_out_of_plane_deg": out_deg}
        if u is not None and out_deg <= 20.0:
            bd_deg = float(np.degrees(np.arctan2(u[1], u[0])))
            # chords only in grains >= min_ecd (fragments end chords like any boundary but are not counted)
            count = np.zeros(int(max(pg.max(initial=0), rg["id"].max(initial=0))) + 1, bool)
            count[rg["id"][(rg["ecd_um"] >= min_ecd) & (rg["n_px"] >= p.min_px)].astype(np.int64)] = True
            for name, ang in (("across_bd", bd_deg + 90.0), ("along_bd", bd_deg)):
                c = directional_intercepts(bgi, pg, ang, p.beta_line_spacing_um, sample, count)
                full = ~c["truncated"]
                beta_chords[f"{name}_um"] = c["length_um"][full]
                beta_chords[f"{name}_label"] = c["label"][full]
                beta_table[f"mean_chord_{name}_um"] = per_label_mean(c["label"][full], c["length_um"][full],
                                                                     beta_table["id"])
                beta_part[name] = {"direction_deg": c["direction_deg"], "mean_e112_um": c["mean_e112_um"],
                                   "n_complete": c["n_complete"], "n_truncated": c["n_truncated"],
                                   # length-weighted fit: ragged lath-scale boundaries between large grains
                                   # give many short chords that dominate the number distribution (D14)
                                   "complete_chords": dist_stats(c["length_um"][full], c["length_um"][full])}
            n_trunc = beta_part["along_bd"]["n_truncated"]
            if n_trunc > beta_part["along_bd"]["n_complete"]:
                warnings.append("most chords along BD are cut by the map edge: prior-beta length along BD is a "
                                "lower bound (columnar grains longer than the map)")
        else:
            warnings.append(f"build direction is {out_deg:.0f}° out of the map plane: no intercepts along/across BD")

    p.grains, p.recon = gsrc, rid               # resolved steps: replay passes them explicitly

    # -- summary table ----------------------------------------------------------------------------
    rules = _rules(p)
    rows = [
        _row("alpha_ecd", "um", "ECD = 2 sqrt(A / pi) of alpha grains (MTEX area); number- and area-weighted", s_alpha,
             rules=rules, partition=f"grains.calc {gsrc}: HAGB {gp.hagb_deg:g}°"),
        _row("lath_thickness_intercept", "um", "per lath: median chord of test lines perpendicular to its long "
             f"axis (laths: major/minor >= {p.lath_min_aspect:g}; line spacing {p.lath_line_spacing_um or step:g} um)",
             s_thick, rules=rules),
        _row("lath_thickness_minor_axis", "um", "minor axis 2b of the second-moment ellipse (= 1.155 x width "
             "of a rectangle)", s_minor, rules=rules),
        _row("lath_chords_pooled", "um", "all complete chords perpendicular to the lath long axes (mean intercept)",
             s_chord, rules=rules),
        _row("colony_ecd", "um", f"ECD of colonies: alpha grains merged across Burgers 10.53° boundaries (mean "
             f"orientations within {p.colony.burgers_tol_deg:g}°)", s_colony, rules=rules)]
    if beta_part:
        rows.append(_row("prior_beta_ecd", "um", "ECD of prior-beta grains (reconstruction)", beta_part["ecd"],
                         rules=f"{rules}; ECD >= {beta_part['min_ecd_um']:g} um"))
        for name, lab in (("across_bd", "width"), ("along_bd", "length")):
            if name in beta_part:
                b = beta_part[name]
                rows.append(_row(f"prior_beta_{lab}_intercept", "um",
                                 f"complete chords {name.replace('_', ' ')} in grains with ECD >= "
                                 f"{min_ecd:g} um (direction {b['direction_deg']:.1f}°, spacing "
                                 f"{p.beta_line_spacing_um:g} um); E112 mean incl. edge chords counted half; "
                                 "area_weighted_* columns = line-length-weighted",
                                 b["complete_chords"], e112_mean=b["mean_e112_um"], n_truncated=b["n_truncated"]))
        cs = beta_part["caliper"]
        for key, lab in (("across_bd_um", "width"), ("along_bd_um", "length")):
            if cs.get(key):
                rows.append(_row(f"prior_beta_{lab}_caliper", "um", "per-grain caliper " + key[:-3].replace("_", " "),
                                 {"n": cs["n_used"], "mean": cs[key]["mean"], "median": cs[key]["median"],
                                  "area_weighted": {"mean": cs[key]["area_weighted_mean"],
                                                    "lognormal": {"mu": None, "sigma": None}}},
                                 rules=f"{rules}; ECD >= {cs['min_ecd_um']:g} um"))
    for k in ("loaded", "source_map"):
        rows.append(_row(f"retained_beta_fraction_{k}", "1", "indexed beta pixels / indexed pixels (area fraction)",
                         None, value=rb[k]["fraction"], note=RESOLUTION_NOTE))
    summary_table = _rows_to_table(rows)

    summary = {"backend": ctx.backend.name, "map_id": p.map_id, "input_state": sheet.input_state,
               "grains": gsrc, "source_map": msrc, "step_um": step, "grid": grid, "rules": rules,
               "lath_partition": gp.model_dump(mode="json"), "params": p.model_dump(mode="json"),
               "alpha": {"n_grains": int(is_alpha.sum()), "n_used": n_used, "n_laths": int(is_lath.sum()),
                         "mean_px_per_used_grain": mean_px, "ecd": s_alpha, "thickness_intercept": s_thick,
                         "thickness_minor_axis": s_minor, "chords_pooled": s_chord},
               "colony": {"n_colonies": int(c_alpha.sum()), "n_used": int(c_used.sum()),
                          "n_alpha_grains_merged": n_merged, "ecd": s_colony},
               "prior_beta": beta_part, "retained_beta": rb, "warnings": warnings}
    ctx.params.update(grains=gsrc, recon=rid)
    ctx.metrics.update(n_laths=int(is_lath.sum()), n_colonies=int(c_used.sum()))
    sheets = {"Summary": summary_table, "Alpha grains": lath_table, "Colonies": colony_table}
    if beta_table is not None:
        sheets["Prior beta"] = beta_table
    sheets["Parameters"] = {"parameter": np.array(list(_flat(summary["params"])), dtype=object),
                            "value": np.array([str(v) for v in _flat(summary["params"]).values()], dtype=object)}
    ctx.output("summary", "json", _json(summary))
    ctx.output("summary_table", "csv", summary_table)
    ctx.output("alpha_grains", "csv", lath_table)
    ctx.output("colonies", "csv", colony_table)
    if beta_table is not None:
        ctx.output("prior_beta", "csv", beta_table)
    ctx.output("workbook", "xlsx", sheets)
    ctx.output("pixels", "npz", {"colony_id": cpix, "lath_colony": lath_colony})
    ctx.output("chords", "npz", {"lath_label": li["chord_label"], "lath_um": li["chord_um"], **beta_chords})


def _flat(d: dict, pre: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{pre}{k}."))
        else:
            out[f"{pre}{k}"] = v
    return out


# ---- grainsize.figure ---------------------------------------------------------------------------
GS_FIGURES = ("alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd", "colony_ecd_area",
              "beta_ecd", "beta_width", "beta_length", "colony_map")


class GrainSizeFigureParams(BaseModel):
    map_id: str
    kind: Literal["alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd",
                  "colony_ecd_area", "beta_ecd", "beta_width", "beta_length", "colony_map"]
    grainsize: str | None = None              # grainsize.run step (default newest of the map)
    width_cm: float | None = None
    dpi: int | None = None


def _read_csv(ctx: OpContext, step, name: str) -> dict[str, np.ndarray]:
    import pandas as pd  # noqa: PLC0415

    df = pd.read_csv(ctx.project.root / step.outputs[name].path)
    return {c: df[c].to_numpy() for c in df.columns}


def _hist_chart(v: np.ndarray, w: np.ndarray | None, xlabel: str, title: str,
                weight_label: str = "area") -> ChartSpec:
    """Number histogram (count) with the number-weighted log-normal fit, or, with weights, the area-weighted
    histogram (% of area) with the area-weighted log-normal fit."""
    v = np.asarray(v, float)
    ok = np.isfinite(v) & (v > 0)
    v = v[ok]
    w = None if w is None else np.asarray(w, float)[ok]
    e = hist_edges(v)
    bw = float(e[1] - e[0])
    xs = np.linspace(e[0] + bw / 20, e[-1], 300)
    s = dist_stats(v, w)
    if w is None:
        h, _ = np.histogram(v, e)
        curves = [(xs, lognormal_pdf(xs, s["lognormal"]["mu"], s["lognormal"]["sigma"]) * v.size * bw,
                   f"log-normal fit (median {s['lognormal']['median']:.2f}, σ {s['lognormal']['sigma']:.2f})")] \
            if s and s["lognormal"]["sigma"] > 0 else []
        ylabel = "count"
    else:
        h, _ = np.histogram(v, e, weights=w)
        h = 100 * h / w.sum()
        aw = s["area_weighted"]["lognormal"] if s else None
        curves = [(xs, lognormal_pdf(xs, aw["mu"], aw["sigma"]) * 100 * bw,
                   f"{weight_label}-weighted log-normal (median {aw['median']:.2f}, σ {aw['sigma']:.2f})")] \
            if aw and aw["sigma"] > 0 else []
        ylabel = f"{weight_label} fraction (%)"
    return ChartSpec((e[:-1] + e[1:]) / 2, h.astype(float), bw, xlabel, ylabel, title, curves=curves)


def golden_colors(ids: np.ndarray) -> np.ndarray:
    """Distinct, deterministic colours per id (golden-ratio hue steps), same in both backends."""
    h = (np.asarray(ids, float) * 0.6180339887) % 1.0
    s = 0.45 + 0.35 * ((np.asarray(ids) * 7) % 3) / 2
    return np.array([colorsys.hsv_to_rgb(a, b, 0.95) for a, b in zip(h, s)]).reshape(-1, 3)


@op("grainsize.figure", GrainSizeFigureParams, uses_backend=True)
def _grainsize_figure(ctx: OpContext) -> None:
    """Histograms with log-normal fits (alpha ECD number / area-weighted, lath thickness by intercept and by
    minor axis, colony ECD number / area-weighted, prior-beta ECD, width and length intercepts relative to the
    build direction) and the colony map; drawn by the backend that computed the grain-size step."""
    from .settings import load_settings  # noqa: PLC0415

    p = GrainSizeFigureParams.model_validate(ctx.params)
    sid = resolve_source(ctx, p.map_id, p.grainsize, ("grainsize.run",))
    s = ctx.project.recipe().step(sid)
    if s.backend != ctx.backend.name:
        raise ValueError(f"grain size {sid} was computed by {s.backend}; a figure always comes from the engine that "
                         f"computed it: use backend={s.backend!r} or run grainsize.run again")
    ctx.step.inputs.append(f"{sid}:summary")
    style = load_settings().figure
    width, dpi = p.width_cm or style.width_cm, p.dpi or style.dpi
    out_png = ctx.out_dir / "figure.png"
    summ = json.loads((ctx.project.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    meta: dict[str, Any] = {"kind": p.kind, "map_id": p.map_id, "grainsize": sid, "backend": ctx.backend.name}
    n_rule = summ["rules"]
    if p.kind == "colony_map":
        gstep = ctx.project.recipe().step(summ["grains"])
        gz = step_npz(ctx, summ["grains"], "grains")
        px = step_npz(ctx, sid, "pixels")
        cpix, lc = px["colony_id"].astype(np.int64), px["lath_colony"].astype(np.int64)
        rgb = np.full((cpix.size, 3), np.nan)
        m = cpix > 0
        uc = np.unique(cpix[m])
        cols = golden_colors(uc)
        lut = np.zeros(int(uc.max(initial=0)) + 1, np.int64)
        lut[uc] = np.arange(uc.size)
        rgb[m] = cols[lut[cpix[m]]]
        g1, g2 = gz["gb_grain1"].astype(np.int64), gz["gb_grain2"].astype(np.int64)
        inside = (g1 > 0) & (g2 > 0) & ~gz["gb_inner"].astype(bool)
        c1 = np.where(inside, lc[np.clip(g1, 0, lc.size - 1)], 0)
        c2 = np.where(inside, lc[np.clip(g2, 0, lc.size - 1)], 0)
        same = inside & (c1 == c2) & (c1 > 0)
        diff = inside & ~same
        sel = same | diff
        seg = {k: gz[f"gb_{k}"][sel] for k in ("x1", "y1", "x2", "y2")}
        srgb = np.where(same[sel][:, None], 0.55, 0.0) * np.ones((1, 3))
        hagb = gstep.params["grains"]["hagb_deg"]
        spec = RenderSpec(rgb=rgb, seg=seg, seg_rgb=srgb, seg_width=style.boundary_line_width, width_cm=width, dpi=dpi,
                          scale_bar=style.scale_bar, title=f"colonies (n = {summ['colony']['n_colonies']})",
                          legend=[("colony boundary", (0.0, 0.0, 0.0)),
                                  (f"alpha grain boundary ≥ {hagb:g}° inside a colony", (0.55, 0.55, 0.55))])
        ctx.backend.render(ctx.use(summ["source_map"]), spec, out_png)
    else:
        if p.kind in ("beta_ecd", "beta_width", "beta_length") and not summ.get("prior_beta"):
            raise ValueError("no prior-beta part in this grain-size step: run recon.run, then grainsize.run")
        if p.kind in ("alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis"):
            t = _read_csv(ctx, s, "alpha_grains")
            used, lath = t["used"].astype(bool), t["is_lath"].astype(bool)
            if p.kind == "alpha_ecd":
                c = _hist_chart(t["ecd_um"][used], None, "alpha grain ECD (µm)", f"alpha grains, {n_rule}")
            elif p.kind == "alpha_ecd_area":
                c = _hist_chart(t["ecd_um"][used], t["area_um2"][used], "alpha grain ECD (µm)",
                                "alpha grains, area-weighted")
            elif p.kind == "lath_thickness":
                c = _hist_chart(t["thickness_intercept_um"][lath], None, "lath thickness, intercept ⊥ long axis (µm)",
                                f"alpha laths (aspect ≥ {summ['params']['lath_min_aspect']:g}), median chord per lath")
            else:
                c = _hist_chart(t["ellipse_minor_um"][lath], None, "lath minor axis 2b (µm)",
                                "alpha laths, second-moment ellipse")
        elif p.kind in ("colony_ecd", "colony_ecd_area"):
            t = _read_csv(ctx, s, "colonies")
            used = t["used"].astype(bool)
            c = _hist_chart(t["ecd_um"][used], t["area_um2"][used] if p.kind == "colony_ecd_area" else None,
                            "colony ECD (µm)", "colonies" + (", area-weighted" if p.kind == "colony_ecd_area" else ""))
        elif p.kind == "beta_ecd":
            t = _read_csv(ctx, s, "prior_beta")
            used = t["used"].astype(bool)
            c = _hist_chart(t["ecd_um"][used], None, "prior-β grain ECD (µm)",
                            f"prior-β grains, ECD ≥ {summ['prior_beta']['min_ecd_um']:g} µm, {n_rule}")
        else:
            name = "across_bd" if p.kind == "beta_width" else "along_bd"
            ch = step_npz(ctx, sid, "chords")
            if f"{name}_um" not in ch:
                raise ValueError("no intercepts relative to the build direction in this step")
            b = summ["prior_beta"][name]
            c = _hist_chart(ch[f"{name}_um"], ch[f"{name}_um"],
                            f"prior-β {'width (chords across BD)' if name == 'across_bd' else 'length (chords along BD)'} (µm)",
                            f"complete chords, length-weighted; E112 mean {b['mean_e112_um']:.1f} µm "
                            f"({b['n_truncated']} edge chords counted half)", "line length")
        c.width_cm, c.dpi = width, dpi
        meta["n"] = int(np.isfinite(np.asarray(c.heights, float)).sum())
        ctx.backend.render_chart(c, out_png)
    ctx.params.update(grainsize=sid)
    ctx.output("figure", "png", out_png)
    ctx.output("meta", "json", _json(meta))


# ---- grainsize.compare_oim ----------------------------------------------------------------------
class CompareOimGrainsParams(BaseModel):
    map_id: str
    file: str                                 # OIM Analysis grain file export (text)
    grainsize: str | None = None              # grainsize.run step (default newest of the map)
    phase: int | None = None                  # OIM phase number to keep (default: all rows)


@op("grainsize.compare_oim", CompareOimGrainsParams, uses_backend=True)
def _grainsize_compare_oim(ctx: OpContext) -> None:
    """Validate the alpha grain ECD against an OIM Analysis grain file: the same rules (minimum pixels if OIM
    gives the point count, edge grains excluded if OIM flags them) are applied to the OIM grains; number- and
    area-weighted means, log-normal fits and a two-sample KS test are compared; histogram with the OIM
    distribution as a line, drawn by the backend that computed the grain-size step."""
    from scipy import stats as st  # noqa: PLC0415

    p = CompareOimGrainsParams.model_validate(ctx.params)
    sid = resolve_source(ctx, p.map_id, p.grainsize, ("grainsize.run",))
    s = ctx.project.recipe().step(sid)
    if s.backend != ctx.backend.name:
        raise ValueError(f"grain size {sid} was computed by {s.backend}; use backend={s.backend!r}")
    ctx.add_input(p.file)
    summ = json.loads((ctx.project.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    gp = summ["params"]
    oim = read_oim_grain_file(p.file)
    keep = np.ones(oim["ecd_um"].size, bool)
    applied = []
    if p.phase is not None and "phase" in oim:
        keep &= oim["phase"] == p.phase
        applied.append(f"phase {p.phase}")
    if "n_px" in oim:
        keep &= oim["n_px"] >= gp["min_px"]
        applied.append(f">= {gp['min_px']} px")
    if gp["exclude_edge"] and "is_edge" in oim:
        keep &= oim["is_edge"] == 0
        applied.append("edge grains excluded")
    e_oim = oim["ecd_um"][keep]
    a_oim = oim["area_um2"][keep] if "area_um2" in oim else np.pi * e_oim ** 2 / 4
    t = _read_csv(ctx, s, "alpha_grains")
    used = t["used"].astype(bool)
    e_app, a_app = t["ecd_um"][used], t["area_um2"][used]
    s_app, s_oim = dist_stats(e_app, a_app), dist_stats(e_oim, a_oim)
    ks = st.ks_2samp(e_app, e_oim) if e_app.size and e_oim.size else None
    res = {"oim_file": p.file, "oim_columns_matched": oim["matched"], "oim_rules_applied": applied,
           "app": s_app, "oim": s_oim, "ks_2samp": {"D": float(ks.statistic), "p": float(ks.pvalue)} if ks else None,
           "ratio_mean": s_app["mean"] / s_oim["mean"] if s_app and s_oim else None,
           "ratio_area_weighted_mean": (s_app["area_weighted"]["mean"] / s_oim["area_weighted"]["mean"]
                                        if s_app and s_oim else None),
           "lath_partition": summ["lath_partition"], "note": "OIM grain definition (tolerance, min size) must match "
           "grains.calc (hagb_deg, min_px) for a like-for-like comparison; OIM grain file format not yet verified (D14)"}
    c = _hist_chart(e_app, None, "alpha grain ECD (µm)", f"app ({ctx.backend.name}) vs OIM")
    edges = np.r_[c.centers - c.bar_width / 2, c.centers[-1] + c.bar_width / 2]
    ho, _ = np.histogram(e_oim, edges)
    c.curves = [(c.centers, ho.astype(float), f"OIM (n = {e_oim.size})")] + c.curves
    from .settings import load_settings  # noqa: PLC0415

    style = load_settings().figure
    c.width_cm, c.dpi = style.width_cm, style.dpi
    out_png = ctx.out_dir / "comparison.png"
    ctx.backend.render_chart(c, out_png)
    ctx.params.update(grainsize=sid)
    ctx.output("result", "json", _json(res))
    ctx.output("comparison", "png", out_png)
