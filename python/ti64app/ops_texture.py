"""Phase 6.7 operations: texture (ODF, pole figures, inverse pole figures) and Young's modulus from texture.

texture.run       ODF of an orientation set (ti64app.texture: phase, region, weighting, specimen frame) built by the
                  active backend; statistics (max MRD, texture index, grains used, halfwidth), PF / IPF values on fixed
                  grids (conformance) and the PF / IPF figures drawn by the backend. 'only' (pf | ipf) = draw just
                  that figure as 'figure' (Save / Export of a result, service.render_figure_as).
texture.export_odf  the ODF of a texture.run step in MTEX's text format (export log, not a recipe step).
elastic.run       Voigt / Reuss / Hill stiffness of the polycrystal (alpha + retained beta mixed by area fraction;
                  single-crystal presets from Settings), E along BD / beam axis / width / custom directions with a
                  bootstrap over grains (+- standard deviation), anisotropy Emax / Emin, stereographic + 3D E plot.
Units GPa; porosity ignored.
"""
from __future__ import annotations

import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from . import elastic as el
from . import progress
from . import texture as tx
from .backends.base import ElasticFigureSpec, TextureFigureSpec
from .service import OpContext, op

Region = Literal["map", "roi", "sample"]
Weighting = Literal["pixel", "grain"]


class TextureInput(BaseModel):
    map_id: str
    phase: Literal["alpha", "beta"] = "alpha"          # beta = reconstructed prior beta
    region: Region = "map"
    roi_id: str | None = None
    weighting: Weighting = "pixel"
    resolved: dict | None = None                      # filled in: the steps used per map


class TextureParams(TextureInput):
    halfwidth_deg: float = Field(5.0, gt=0.5, le=30)
    poles: list[str] | None = None                    # default: all poles of the phase
    ipf_dirs: list[str] = Field(default_factory=lambda: ["BD", "beam axis", "width"])
    custom_vector: tuple[float, float, float] | None = None
    plot: Literal["density", "scatter", "both"] = "density"
    scatter_points: int = Field(5000, ge=100, le=200_000)
    only: Literal["pf", "ipf"] | None = None          # Save / Export of one figure


def _maps_of(ctx: OpContext, p: TextureInput) -> list[str]:
    if p.region != "sample":
        return [p.map_id]
    sid = ctx.project.sample_of(p.map_id)
    if not sid:
        raise tx.TextureInputError(f"{p.map_id} belongs to no sample (Files tab): 'all maps of the sample' needs one")
    return [m.id for m in ctx.project.files() if m.sample_id == sid and m.kind == "ang"]


def orientation_set(ctx: OpContext, p: TextureInput, phase: str | None = None) -> tx.OrientationSet:
    """The orientation set of the inputs (recorded steps are reused on replay through p.resolved)."""
    phase = phase or p.phase
    sets = []
    res = dict(p.resolved or {})
    for mid in _maps_of(ctx, p):
        r = res.get(f"{phase}:{mid}", {})
        try:
            s, used = tx.map_set(ctx.project, mid, phase, p.weighting, p.roi_id if p.region == "roi" else None,
                                 source=r.get("source"), grains=r.get("grains"), recon=r.get("recon"))
        except tx.TextureInputError:
            if p.region == "sample" and mid != p.map_id:
                continue                                    # a map of the sample that is not loaded / reconstructed
            raise
        res[f"{phase}:{mid}"] = used
        for k in ("source", "grains", "recon"):
            if used.get(k):
                ctx.step.inputs.append(f"{used[k]}:{'recon' if k == 'recon' else ('grains' if k == 'grains' else 'ebsd')}")
        sets.append(s)
    ctx.params["resolved"] = res
    return sets[0] if len(sets) == 1 else tx.combine(sets)


def _handle(ctx: OpContext, s: tx.OrientationSet):
    """A backend map object of the (first) map: crystal symmetries of the phases."""
    from .layers import _any_map_handle  # noqa: PLC0415

    return _any_map_handle(ctx, s.maps[0])


def _phase_name(phase: str) -> str:
    from .phases import ALPHA, BETA  # noqa: PLC0415

    return ALPHA if phase == "alpha" else BETA


def _ipf_vectors(s: tx.OrientationSet, names: list[str], custom) -> list[tuple[str, tuple]]:
    out = []
    for n in names:
        if n == "custom":
            if custom is None:
                raise tx.TextureInputError("custom IPF direction: enter the vector")
            out.append((f"custom {tuple(round(c, 3) for c in custom)}", tuple(custom)))
        elif n in s.frame.directions:
            out.append((n, s.frame.directions[n]))
        else:
            s.warnings.append(f"IPF direction '{n}' is not available in this frame "
                              f"({', '.join(s.frame.directions)}): skipped")
    if not out:
        raise tx.TextureInputError("no inverse-pole-figure direction available (enter the build direction or the "
                                   "map position on the Files tab, or a custom vector)")
    return out


@op("texture.run", TextureParams, uses_backend=True)
def _texture_run(ctx: OpContext) -> None:
    """Texture of alpha or reconstructed prior-beta orientations (whole map, an ROI or all maps of a sample; per pixel
    = area weighted, or one point per grain) in the specimen frame: kernel-density ODF (halfwidth, default 5°), max
    MRD, texture index, grains used; pole figures (alpha {0001} {10-10} {11-20} {10-11}, beta {100} {110} {111}) and
    inverse pole figures (BD, beam axis, width, custom) as density (MRD) and / or scatter, equal-area, upper
    hemisphere, antipodal, one colour range per figure. Warns when few grains make the statistics uncertain."""
    from .settings import load_settings  # noqa: PLC0415

    p = TextureParams.model_validate(ctx.params)
    progress.report("collecting orientations", 0.1)
    s = tx.subsample(orientation_set(ctx, p))
    phase = _phase_name(p.phase)
    h = _handle(ctx, s)
    progress.report("building the ODF", 0.3)
    odfh, st = ctx.backend.odf_build(h, phase, s.quat, s.weights, p.halfwidth_deg)
    poles = tx.poles_for(p.phase, p.poles)
    ipfs = _ipf_vectors(s, p.ipf_dirs, p.custom_vector)
    style = load_settings().figure
    rng = np.random.default_rng(0)
    sc = None
    if p.plot in ("scatter", "both"):
        src_q = s.grain_quat if p.weighting == "grain" and s.grain_quat is not None else s.quat
        k = min(len(src_q), p.scatter_points)
        sc = src_q[np.sort(rng.choice(len(src_q), k, replace=False))]
    common = {"mode": p.plot, "scatter_quat": sc, "axis_labels": s.frame.labels, "dpi": min(style.dpi, 300)}
    pf = TextureFigureSpec("pf", [{"label": k_, "hkl": v} for k_, v in poles.items()],
                           title=f"{'α' if p.phase == 'alpha' else 'prior β'} pole figures, {s.frame.kind} frame",
                           width_cm=4.2 * len(poles) + 2, **common)
    ipf = TextureFigureSpec("ipf", [{"label": lab, "vector": v} for lab, v in ipfs],
                            title=f"{'α' if p.phase == 'alpha' else 'prior β'} inverse pole figures",
                            width_cm=4.2 * len(ipfs) + 2, **common)
    ext = ctx.figure_ext
    if p.only:
        progress.report("drawing", 0.8)
        out = ctx.out_dir / f"figure{ext}"
        ctx.backend.render_texture(odfh, pf if p.only == "pf" else ipf, out)
        ctx.output("figure", "png" if ext == ".png" else ext.lstrip("."), out)
        ctx.backend.release(odfh)
        return
    # numbers on fixed grids (conformance: compare the backends; the figures are compared by eye)
    progress.report("pole figure densities", 0.5)
    dirs, _X, _Y = tx.equal_area_grid(16, 49)
    vals = {f"pf_{k_}": ctx.backend.odf_pf(odfh, v, dirs) for k_, v in poles.items()}
    from .backends.pymtex_texture import sector_grid  # noqa: PLC0415

    sec, _a, _b, _c = sector_grid("cubic" if p.phase == "beta" else "hexagonal", 9, 9)
    for lab, v in ipfs:
        vals[f"ipf_{lab}"] = ctx.backend.odf_ipf(odfh, v, sec)
    pf_max = {k_[3:]: float(np.nanmax(v)) for k_, v in vals.items() if k_.startswith("pf_")}
    progress.report("drawing", 0.8)
    out_pf, out_ipf = ctx.out_dir / f"pf{ext}", ctx.out_dir / f"ipf{ext}"
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    ctx.backend.render_texture(odfh, pf, out_pf)
    ctx.backend.render_texture(odfh, ipf, out_ipf)
    ctx.backend.release(odfh)
    summary = {**s.summary(), "halfwidth_deg": p.halfwidth_deg, "max_mrd": st["max_mrd"],
               "texture_index": st["texture_index"], "pf_max_mrd": pf_max, "poles": list(poles),
               "ipf_dirs": [lab for lab, _v in ipfs], "weighting": p.weighting, "region": p.region,
               "backend": ctx.backend.name}
    ctx.metrics.update(max_mrd=st["max_mrd"], texture_index=st["texture_index"],
                       n_grains=float(s.n_grains or 0))
    ctx.output("summary", "json", summary)
    ctx.output("values", "npz", {k_.replace(" ", "_"): v for k_, v in vals.items()} | {"pf_dirs": dirs,
                                                                                         "ipf_dirs": sec})
    ctx.output("orientations", "npz", {"quat": s.quat, "weights": s.weights,
                                       **({"grain_quat": s.grain_quat, "grain_weights": s.grain_weights}
                                          if s.grain_quat is not None else {})})
    ctx.output("pf", "png", out_pf)
    ctx.output("ipf", "png", out_ipf)
    for k, o in (("pf", out_pf), ("ipf", out_ipf)):          # MATLAB: also the editable .fig (XRD_part2.m)
        if o.with_suffix(".fig").is_file():
            ctx.output(f"{k}_fig", "file", o.with_suffix(".fig"), f"{k}_matlab")


class TextureExportParams(BaseModel):
    texture: str                                      # texture.run step id
    map_id: str | None = None


@op("texture.export_odf", TextureExportParams, uses_backend=True)
def _texture_export_odf(ctx: OpContext) -> None:
    """The ODF of a texture.run step in MTEX's text format (loadODF / importODF), with a JSON sidecar (halfwidth,
    frame, grains used). Rebuilt from the step's stored orientations by the backend that made it."""
    p = TextureExportParams.model_validate(ctx.params)
    s = ctx.project.recipe().step(p.texture)
    if s.op != "texture.run":
        raise ValueError(f"step {p.texture} is {s.op}, not a texture result")
    tp = TextureParams.model_validate(s.params)
    ctx.params["map_id"] = tp.map_id
    ctx.step.inputs.append(f"{p.texture}:orientations")
    with np.load(ctx.project.root / s.outputs["orientations"].path) as z:
        q, w = z["quat"], z["weights"]
    summ = json.loads((ctx.project.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
    from .layers import _any_map_handle  # noqa: PLC0415

    h = _any_map_handle(ctx, summ["maps"][0])
    odfh, _st = ctx.backend.odf_build(h, _phase_name(tp.phase), q, w, tp.halfwidth_deg)
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    out = ctx.out_dir / "export.txt"
    ctx.backend.odf_export(odfh, out)
    ctx.backend.release(odfh)
    ctx.output("export", "file", out)
    ctx.output("sidecar", "json", {"texture_step": p.texture, "phase": tp.phase, "halfwidth_deg": tp.halfwidth_deg,
                                   "weighting": tp.weighting, "region": tp.region, "maps": summ["maps"],
                                   "frame": summ["frame"], "n_grains": summ["n_grains"],
                                   "backend": ctx.backend.name, "format": "MTEX ODF text (phi1 Phi phi2 value, Bunge)"})


# ---- elastic properties -------------------------------------------------------------------------------------------
class ElasticParams(TextureInput):
    preset_alpha: str | None = None                   # Settings preset name (None = default)
    preset_beta: str | None = None
    include_beta: bool = True                         # retained beta mixed in by its area fraction
    directions: list[str] = Field(default_factory=lambda: ["BD", "beam axis", "width"])
    custom_vector: tuple[float, float, float] | None = None
    bootstrap_runs: int = Field(200, ge=0, le=5000)
    seed: int = 0
    constants: dict | None = None                     # filled in: the constants used (replay exact)


@op("elastic.run", ElasticParams, uses_backend=True)
def _elastic_run(ctx: OpContext) -> None:
    """Young's modulus from texture: single-crystal stiffness (Settings presets, GPa) rotated into the specimen frame
    for every orientation and averaged (Voigt = mean stiffness, Reuss = inverse mean compliance, Hill = their mean;
    area weighted; alpha and retained beta mixed by area fraction). Outputs the 6x6 matrices (Voigt notation, specimen
    frame), E along BD / beam axis / width / custom (+- standard deviation from a bootstrap over grains), Emax / Emin
    and a stereographic + 3D plot of E(direction) drawn by the backend. Porosity is ignored."""
    from .settings import load_settings  # noqa: PLC0415

    p = ElasticParams.model_validate(ctx.params)
    if p.phase != "alpha":
        raise tx.TextureInputError("Young's modulus at room temperature comes from the alpha (+ retained beta) "
                                   "orientations; the reconstructed prior-beta map describes the high-temperature "
                                   "phase. Choose α.")
    es = load_settings().elastic
    if p.constants:                                   # replay: the recorded constants
        consts = {k: el.ElasticPreset.model_validate(v) for k, v in p.constants.items()}
    else:
        consts = {"alpha": es.preset(p.preset_alpha, "alpha")}
        if p.include_beta:
            consts["beta"] = es.preset(p.preset_beta, "beta")
    progress.report("collecting orientations", 0.1)
    sa = orientation_set(ctx, p, "alpha")
    sets = {"alpha": sa}
    if "beta" in consts:
        try:
            sets["beta"] = orientation_set(ctx, p, "beta_retained")
        except (tx.TextureInputError, KeyError, ValueError):
            pass
    h = _handle(ctx, sa)
    parts, extra, notes = [], None, list(sa.warnings)
    area = {k: float(s.weights.sum()) for k, s in sets.items()}
    tot = sum(area.values())
    progress.report("rotating stiffness tensors", 0.3)
    for k, s in sets.items():
        C, S = ctx.backend.stiffness_rotate(h, _phase_name(k), s.quat, consts[k].matrix(), s.weights)
        parts.append((area[k] / tot, C, S))
        if k == "beta":
            extra = (area[k] / tot, C, S)
    Cm, Sm = el.mix_phases(parts)
    av = el.averages(Cm, Sm)
    dirs = []
    for n in p.directions:
        if n == "custom":
            if p.custom_vector is None:
                raise tx.TextureInputError("custom direction: enter the vector")
            dirs.append((f"custom {tuple(round(c, 3) for c in p.custom_vector)}", tuple(p.custom_vector)))
        elif n in sa.frame.directions:
            dirs.append((n, sa.frame.directions[n]))
        else:
            notes.append(f"direction '{n}' not available ({', '.join(sa.frame.directions)}): skipped")
    D = np.array([v for _n, v in dirs], float).reshape(-1, 3)
    E = {m: el.youngs_modulus(av[m], D) if len(D) else np.zeros(0) for m in av}
    boot = None
    if p.bootstrap_runs and sa.grain_quat is not None and len(sa.grain_quat) > 1 and len(D):
        progress.report(f"bootstrap ({p.bootstrap_runs} runs)", 0.6)
        Cg, Sg = ctx.backend.stiffness_rotate(h, _phase_name("alpha"), sa.grain_quat, consts["alpha"].matrix())
        boot = el.bootstrap(Cg, Sg, sa.grain_weights, D, p.bootstrap_runs, p.seed, extra)
    elif p.bootstrap_runs:
        notes.append("no bootstrap: grains are needed (run 'Grains and boundaries')")
    aniso = {m: el.anisotropy(av[m]) for m in av}
    table = []
    for i, (n, v) in enumerate(dirs):
        row = {"direction": n, "vector": [round(c, 4) for c in v]}
        for m in ("voigt", "reuss", "hill"):
            row[f"E_{m}"] = round(float(E[m][i]), 2)
            if boot is not None:
                row[f"sd_{m}"] = round(float(boot[m][:, i].std(ddof=1)), 2)
        table.append(row)
    # figure: Hill E(direction)
    progress.report("drawing", 0.85)
    dg, rp = el.direction_grid(46, 181)
    Eg = el.youngs_modulus(av["hill"], dg.reshape(-1, 3)).reshape(dg.shape[:2])
    th = np.linspace(0, np.pi, 61)
    ph = np.linspace(0, 2 * np.pi, 121)
    TH, PH = np.meshgrid(th, ph, indexing="ij")
    ds = np.stack([np.sin(TH) * np.cos(PH), np.sin(TH) * np.sin(PH), np.cos(TH)], -1)
    Es = el.youngs_modulus(av["hill"], ds.reshape(-1, 3)).reshape(TH.shape)
    spec = ElasticFigureSpec(Eg, rp, Es, np.stack([TH, PH]), sa.frame.labels,
                             [(n, v, float(E["hill"][i])) for i, (n, v) in enumerate(dirs)],
                             title=f"E (Hill), {', '.join(sa.maps)}; {consts['alpha'].source.split(',')[0]}")
    out = ctx.out_dir / f"figure{ctx.figure_ext}"
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    ctx.backend.render_elastic(spec, out)
    ctx.params["constants"] = {k: v.model_dump(mode="json") for k, v in consts.items()}
    n5 = sa.n_grains_ecd
    summary = {"maps": sa.maps, "frame": sa.frame.as_dict(), "phases": {k: {"area_fraction": round(area[k] / tot, 6),
                                                                            "preset": consts[k].source,
                                                                            "doi": consts[k].doi}
                                                                        for k in sets},
               "n_grains": sa.n_grains, f"n_grains_ecd_ge_{tx.ECD_MIN_UM:g}um": n5,
               "matrices_GPa": {m: np.round(av[m], 3).tolist() for m in av}, "E_table": table,
               "anisotropy": aniso, "bootstrap_runs": p.bootstrap_runs if boot is not None else 0,
               "weighting": p.weighting, "region": p.region, "backend": ctx.backend.name,
               "notes": notes + ["porosity is ignored (fully dense material)",
                                 "Hill = arithmetic mean of the Voigt and Reuss stiffness (Hill 1952)"]}
    if "beta" in consts and "beta" not in sets:
        summary["notes"].append("no retained beta in this map: alpha only")
    ctx.metrics.update({f"E_hill_{r['direction'].replace(' ', '_')}": r["E_hill"] for r in table})
    ctx.output("summary", "json", summary)
    ctx.output("matrices", "npz", {"C_voigt": av["voigt"], "C_reuss": av["reuss"], "C_hill": av["hill"],
                                   **({f"boot_{m}": boot[m] for m in boot} if boot is not None else {})})
    ctx.output("figure", "png" if ctx.figure_ext == ".png" else ctx.figure_ext.lstrip("."), out)
    if ctx.figure_ext == ".png" and out.with_suffix(".fig").is_file():
        ctx.output("figure_fig", "file", out.with_suffix(".fig"), "figure_matlab")


def sample_E_table(project, sample_id: str, direction: str = "beam axis") -> list[dict]:
    """E along one direction (default the beam axis) for every map of a sample, from each map's newest elastic.run
    (Phase 7: E converts strain to stress in the tension / middle / compression maps). Maps without a result are
    listed with E None."""
    rows = []
    for m in project.files():
        if m.sample_id != sample_id or m.kind != "ang":
            continue
        s = next((t for t in reversed(project.recipe().steps) if t.op == "elastic.run" and not t.stale
                  and t.params.get("map_id") == m.id and t.params.get("region") != "sample"), None)
        row = {"map": m.id, "role": m.role, "E_hill": None, "sd": None, "E_voigt": None, "E_reuss": None,
               "n_grains": None, "step": s.id if s else None}
        if s is not None:
            summ = json.loads((project.root / s.outputs["summary"].path).read_text(encoding="utf-8"))
            r = next((r for r in summ["E_table"] if r["direction"] == direction), None)
            if r:
                row.update(E_hill=r["E_hill"], sd=r.get("sd_hill"), E_voigt=r["E_voigt"], E_reuss=r["E_reuss"])
            row["n_grains"] = summ.get("n_grains")
        rows.append(row)
    return rows
