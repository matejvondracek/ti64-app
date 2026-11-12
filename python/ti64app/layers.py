"""Map layers for the viewer and exports: what a map type looks like, coloured by the active backend.

A layer = per-measurement RGBA colours (backend `measurement_colors`, i.e. the backend's IPF key / colour
map) + the brick cell layout of the map (raster.py) + legend / colour-bar info. Overlays (HAGB, LAGB,
alpha/beta interface, Burgers types, prior-beta GB, crack paths) are vector segments (ops_ebsd.
overlay_segments). The figure op, the viewer and export.map all start from the same RenderSpec.

Layers are computed outside the recipe (viewing records nothing); exports are recipe steps.
Colours are cached in projects/<p>/cache/viewer/<key>.npz, keyed by the layer parameters, the recipe
steps they come from, the backend and the figure style, so a layer is coloured once per change.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from . import progress
from .backends import get_backend
from .backends.base import RenderSpec
from .hashing import json_hash
from .project import Project
from .raster import CellLayout, SegmentIndex, cell_layout
from .recipe import RecipeStep
from .settings import IpfKeySetting, load_settings

LayerKind = Literal["ipf", "phase", "ci", "iq", "fit", "prias", "prias_top", "prias_center", "prias_bottom", "kam", "grod", "gos", "gnd",
                    "parent_ipf", "recon_fit", "variant", "packet"]
MAP_KINDS = ("ipf", "phase", "ci", "iq", "fit", "prias", "prias_top", "prias_center", "prias_bottom", "kam", "grod", "gos", "gnd")
PRIAS_KINDS = ("prias_top", "prias_center", "prias_bottom")
RECON_KINDS = ("parent_ipf", "recon_fit", "variant", "packet")
DATA_OPS = ("ebsd.load", "ebsd.cleanup", "grains.calc", "map.compute", "recon.run")   # steps a layer depends on
OVERLAYS = ("hagb", "lagb", "interface", "burgers", "beta_gb", "crack")
LAYER_LABELS = {"ipf": "IPF (alpha / beta)", "phase": "phase", "ci": "CI", "iq": "IQ", "fit": "Fit", "prias": "PRIAS",
                "prias_top": "PRIAS top strip", "prias_center": "PRIAS centre square",
                "prias_bottom": "PRIAS bottom strip",
                "kam": "KAM", "grod": "GROD", "gos": "GOS", "gnd": "GND", "parent_ipf": "prior β IPF (grain mean)",
                "recon_fit": "reconstruction fit", "variant": "α variant ID", "packet": "α packet ID"}
OVERLAY_LABELS = {"hagb": "HAGB", "lagb": "LAGB", "interface": "α/β interface", "burgers": "Burgers types",
                  "beta_gb": "prior β GB", "crack": "crack"}


class LayerParams(BaseModel):
    kind: LayerKind = "ipf"
    source: str | None = None                 # map step (load / cleanup), default newest
    grains: str | None = None                 # grains.calc step for overlays
    values: str | None = None                 # map.compute step for kam/grod/gos/gnd
    recon: str | None = None                  # recon.run step for recon kinds / beta_gb
    phases: list[str] | None = None           # IPF phase filter
    key: str | None = None                    # IPF key override (default: Settings per phase)
    direction: Literal["X", "Y", "Z", "build", "custom"] | None = None
    custom_vector: tuple[float, float, float] | None = None
    ipf_keys: dict[str, IpfKeySetting] | None = None   # per phase (viewer); key / direction above override all
    clim: tuple[float, float] | None = None


def view_context(project: Project, backend: str | None = None):
    """An OpContext that is never recorded (viewer): backend objects are rebuilt from the recipe if needed."""
    from .service import OpContext  # noqa: PLC0415  (service imports the ops, which import this module)

    be = get_backend(backend or project.info().backend)
    reason = be.unavailable_reason()
    if reason:
        from .backends.base import BackendUnavailable  # noqa: PLC0415

        raise BackendUnavailable(f"{be.name} backend unavailable: {reason}")
    step = RecipeStep(op="view", backend=be.name)
    return OpContext(project, step, {}, be, project.cache_dir / "view")


def _recon_spec(ctx, map_id: str, lp: LayerParams):
    """Reconstruction layers without rebuilding the reconstruction: colours of the parent grains through the
    map's own backend handle (same phases, same points), values from the recon.run outputs."""
    from .backends.base import ReconData  # noqa: PLC0415
    from .ops_ebsd import resolve_source, step_npz  # noqa: PLC0415
    from .ops_recon import BETA, discrete_hsv  # noqa: PLC0415
    from .ops_ebsd import _ipf_vector  # noqa: PLC0415

    style = load_settings().figure
    rid = resolve_source(ctx, map_id, lp.recon, ("recon.run",))
    rstep = ctx.project.recipe().step(rid)
    if rstep.backend != ctx.backend.name:
        raise ValueError(f"reconstruction {rid} was computed by {rstep.backend}; a figure always comes from the "
                         f"engine that computed it: switch the backend to {rstep.backend} or reconstruct again")
    src = rstep.params["source"]
    cache: dict = {}

    def handle():
        if "h" not in cache:
            cache["h"] = ctx.use(src)
        return cache["h"]
    ctx.step.inputs.append(f"{rid}:ebsd")
    pmap = step_npz(ctx, rid, "ebsd")
    n = pmap["x"].size
    spec = RenderSpec(width_cm=style.width_cm, dpi=style.dpi, scale_bar=style.scale_bar,
                      seg_width=style.boundary_line_width)
    meta: dict[str, Any] = {"kind": lp.kind, "map_id": map_id, "recon": rid, "source": src, "warnings": []}
    if lp.kind == "parent_ipf":
        s = (lp.ipf_keys or {}).get(BETA) or style.ipf.get(BETA) or IpfKeySetting()
        key, d = lp.key or s.key, lp.direction or s.direction
        vec = _ipf_vector(ctx.project.sample(map_id), d, lp.custom_vector or s.custom_vector)
        rd = ReconData.from_arrays(step_npz(ctx, rid, "recon"))
        pg = pmap["prop_parent_grain"].astype(np.int64)
        # only the crystal symmetry of the map is needed: in the viewer any live object (or the load step) instead of
        # rebuilding the reconstruction's source (load + cleanup again after a restart, fix 3.8)
        hq = handle() if ctx.step.op != "view" or handle_alive(ctx, src) else _any_map_handle(ctx, map_id)
        gcol = ctx.backend.ipf_colors_quat(hq, BETA, rd.grain["mean_quat"], key, vec)
        lut = np.full(max(int(pg.max(initial=0)), int(rd.grain["id"].max(initial=0))) + 1, -1)
        lut[rd.grain["id"]] = np.arange(rd.grain["id"].size)
        rgb = np.full((n, 3), np.nan)
        ok = (pg > 0) & (lut[pg] >= 0)
        rgb[ok] = gcol[lut[pg[ok]]]
        spec.rgb = rgb
        spec.title = f"prior β grains, mean orientation, {key} ∥ {d}"
        meta["keys"] = {BETA: {"key": key, "direction": d, "vector": list(vec)}}
    elif lp.kind == "recon_fit":
        spec.values, spec.cmap = pmap["prop_recon_fit"].astype(float), style.colormap_scalar
        spec.clim = lp.clim or (0.0, 5.0)
        spec.colorbar_label = spec.title = "fit to Burgers OR (°)"
    else:
        v = step_npz(ctx, rid, "variants")[lp.kind].astype(np.int64)
        cols = discrete_hsv(12 if lp.kind == "variant" else 6)
        rgb = np.full((n, 3), np.nan)
        rgb[v > 0] = cols[v[v > 0] - 1]
        spec.rgb = rgb
        spec.legend = [(str(k + 1), tuple(float(c) for c in cols[k])) for k in range(len(cols))]
        spec.title = f"{lp.kind} ID (ideal Burgers, per prior β grain)"
    return spec, handle, meta, rid


def layer_spec(ctx, map_id: str, lp: LayerParams):
    """(RenderSpec without overlays, lazy handle, meta, source step of the pixel data)."""
    from .ops_ebsd import FigureParams, figure_spec  # noqa: PLC0415

    if lp.kind in RECON_KINDS:
        return _recon_spec(ctx, map_id, lp)
    fp = FigureParams(map_id=map_id, kind=lp.kind, source=lp.source, grains=lp.grains, values=lp.values,
                      phases=lp.phases, key=lp.key, direction=lp.direction, custom_vector=lp.custom_vector,
                      ipf_keys=lp.ipf_keys, clim=lp.clim, overlays=[])
    spec, handle, meta = figure_spec(ctx, fp)
    return spec, handle, meta, meta["source"]


# ---- computed layers (viewer) --------------------------------------------------------------------------
@dataclass
class Layer:
    key: str
    colors: np.ndarray                       # (n, 4) uint8
    layout: CellLayout
    info: dict                               # clim, cmap, colorbar (list of hex), legend, title, keys, warnings
    meta: dict = field(default_factory=dict)


@dataclass
class Overlay:
    key: str
    index: SegmentIndex
    widths: float
    legend: list


_LOCK = threading.Lock()
_LAYERS: dict[str, Layer] = {}
_OVERLAYS: dict[str, Overlay] = {}
_LAYOUTS: dict[str, CellLayout] = {}


def _grid_of(project: Project, map_id: str) -> str:
    from .service import latest_step  # noqa: PLC0415

    load = latest_step(project, ("ebsd.load",), map_id=map_id)
    if load is not None and "meta" in load.outputs:
        m = json.loads((project.root / load.outputs["meta"].path).read_text(encoding="utf-8"))
        return m.get("grid", "hex")
    from .io.ang import read_header  # noqa: PLC0415

    return "hex" if read_header(project.sample(map_id).map_file).is_hex else "square"


def layout_for(project: Project, map_id: str, step_id: str, output: str = "ebsd") -> CellLayout:
    """Brick cell layout of the points of a step's map output (cached per step)."""
    k = f"{project.root}|{step_id}"
    if k not in _LAYOUTS:
        progress.report("building grid", 0.6)
        s = project.recipe().step(step_id)
        with np.load(project.root / s.outputs[output].path, allow_pickle=False) as z:
            x, y = z["x"], z["y"]
        _LAYOUTS[k] = cell_layout(x, y, _grid_of(project, map_id))
    return _LAYOUTS[k]


def _op_of(project: Project, step_id: str | None) -> str | None:
    try:
        return project.recipe().step(step_id).op if step_id else None
    except KeyError:
        return None


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(255 * float(v))) for v in c[:3])


def _newest(steps: list[RecipeStep], ops: tuple[str, ...], **match) -> str | None:
    """Id of the newest non-stale step (as service.latest_step, on an already parsed recipe)."""
    live = [s for s in steps if not s.stale and all(s.params.get(k) == v for k, v in match.items())]
    for o in ops:
        hits = [s for s in live if s.op == o]
        if hits:
            return hits[-1].id
    return None


def newest_grains(steps: list[RecipeStep], map_id: str, target: str = "alpha") -> str | None:
    """Newest current grains.calc of a map on the alpha map ('alpha') or on the reconstructed prior-beta map ('beta':
    its source is a recon.run step, fix 3.10)."""
    recon = {s.id for s in steps if s.op == "recon.run"}
    hits = [s for s in steps if s.op == "grains.calc" and not s.stale and s.params.get("map_id") == map_id
            and ((s.params.get("source") in recon) == (target == "beta"))]
    return hits[-1].id if hits else None


def layer_deps(steps: list[RecipeStep], map_id: str, lp: LayerParams, overlay: str | None = None) -> list:
    """The recipe steps a layer (or an overlay) is drawn from, resolved as figure_spec / overlay_segments resolve
    them. Fix 3.8: the cache key used to contain EVERY data step of the map, so a new grains.calc, KAM or
    reconstruction recoloured the IPF map, which rebuilt the backend objects (load + cleanup again: 5 s pymtex, ~30 s
    MATLAB) on the next view."""
    m = {"map_id": map_id}
    if overlay is not None:
        if overlay in ("hagb", "lagb", "interface", "burgers"):
            return [lp.grains or newest_grains(steps, map_id, "alpha")]
        if overlay == "beta_gb":
            return [lp.recon or _newest(steps, ("recon.run",), **m)]
        return []
    if lp.kind in RECON_KINDS:
        return [lp.recon or _newest(steps, ("recon.run",), **m)]
    deps = [lp.source or _newest(steps, ("ebsd.cleanup", "ebsd.load"), **m)]
    if lp.kind in ("kam", "grod", "gos", "gnd"):
        deps.append(lp.values or _newest(steps, ("map.compute",), **m, kind=lp.kind))
    return deps


def _cache_key(project: Project, map_id: str, lp: LayerParams, backend: str, extra: Any = None,
               overlay: str | None = None) -> str:
    deps = layer_deps(project.recipe().steps, map_id, lp, overlay)
    style = load_settings().figure.model_dump(mode="json")
    # only what colours a map: the file, its input state and the build direction (IPF along "build"); a note, the
    # position or the 4PB geometry do not recolour it
    sheet = project.sample(map_id).model_dump(mode="json", include={"map_file", "input_state", "build_direction",
                                                                    "phase_map"})
    if lp.kind.startswith("prias"):                  # the PRIAS export it is read from
        from .io import prias  # noqa: PLC0415

        extra = [extra, [(str(f), f.stat().st_mtime_ns) for f in prias.find_files(sheet["map_file"])]]
    return json_hash([map_id, lp.model_dump(mode="json"), backend, deps, style, sheet, extra])[7:31]


def get_layer(project: Project, map_id: str, lp: LayerParams, backend: str | None = None) -> Layer:
    """Coloured layer (cached in memory and in cache/viewer/)."""
    backend = backend or project.info().backend
    key = _cache_key(project, map_id, lp, backend)
    with _LOCK:
        if key in _LAYERS:
            return _LAYERS[key]
    f = project.cache_dir / "viewer" / f"layer_{key}.npz"
    if f.is_file():
        src_op = _op_of(project, lp.source or (layer_deps(project.recipe().steps, map_id, lp) or [None])[0])
        progress.report("cleanup from cache" if src_op == "ebsd.cleanup" else "reading cached map", 0.5)
        with np.load(f, allow_pickle=False) as z:
            colors = z["colors"]
            info = json.loads(str(z["info"]))
        L = Layer(key, colors, layout_for(project, map_id, info["source"]), info, info.get("meta", {}))
    else:
        ctx = view_context(project, backend)
        spec, _h, meta, src = layer_spec(ctx, map_id, lp)
        progress.report("colouring map", 0.75)
        colors, cinfo = ctx.backend.measurement_colors(spec)
        info = {"title": spec.title, "colorbar_label": spec.colorbar_label,
                "clim": list(cinfo["clim"]) if cinfo.get("clim") else None, "cmap": cinfo.get("cmap"),
                "colorbar": [_hex(c / 255.0) for c in cinfo["colorbar"]] if cinfo.get("colorbar") is not None else None,
                "legend": [(l, _hex(c)) for l, c in spec.legend], "keys": meta.get("keys"),
                "warnings": meta.get("warnings", []), "source": src, "backend": ctx.backend.name,
                "meta": json.loads(json.dumps(meta, default=str))}
        f.parent.mkdir(parents=True, exist_ok=True)
        np.savez(f, colors=colors, info=json.dumps(info))
        L = Layer(key, colors, layout_for(project, map_id, src), info, info["meta"])
    with _LOCK:
        _LAYERS[key] = L
        while len(_LAYERS) > 12:                     # ~10 MB each on the full maps
            _LAYERS.pop(next(iter(_LAYERS)))
    progress.report("drawing", 0.95)                 # the viewer requests its tiles next
    return L


def get_overlay(project: Project, map_id: str, name: str, lp: LayerParams, backend: str | None = None,
                style: dict | None = None) -> Overlay:
    """One overlay class as a binned segment index (crack paths are re-read on every change). style = OverlayStyle
    of this class (colour / width factor, from the viewer settings)."""
    from .annotations import load_annotations  # noqa: PLC0415
    from .ops_ebsd import overlay_segments  # noqa: PLC0415

    backend = backend or project.info().backend
    extra = [name, style] + ([a.model_dump(mode="json") for a in load_annotations(project, map_id)
                              if a.type == "crack"] if name == "crack" else [])
    key = _cache_key(project, map_id, LayerParams(grains=lp.grains, recon=lp.recon), backend, extra, overlay=name)
    with _LOCK:
        if key in _OVERLAYS:
            return _OVERLAYS[key]
    ctx = view_context(project, backend)
    lw = load_settings().figure.boundary_line_width
    ov = overlay_segments(ctx, map_id, [name], lp.grains, lp.recon, lw, {name: style} if style else None)
    seg = ov["seg"] or {k: np.zeros(0) for k in ("x1", "y1", "x2", "y2")}
    O = Overlay(key, SegmentIndex(seg, ov["rgb"]), float(ov["widths"][0]) / lw if len(ov["widths"]) else 1.0,
                [(l, _hex(c)) for l, c in ov["legend"]])
    with _LOCK:
        _OVERLAYS[key] = O
    return O


_PROPS: dict[str, dict] = {}


def stored_data(project: Project, step_id: str) -> dict[str, bool]:
    """Which per-pixel data a map step really contains (cached per step): CI / IQ present, Fit present and not
    constant (the test exports store Fit = 180 everywhere), PRIAS columns in the .ang rows, PRIAS text exports next
    to the .ang (prias_top / prias_center / prias_bottom, only when they fit the map)."""
    s = project.recipe().step(step_id)
    try:                                         # a PRIAS export added / changed later is noticed
        from .io import prias  # noqa: PLC0415

        pf = [(f.name, f.stat().st_mtime_ns) for f in prias.find_files(project.sample(s.params["map_id"]).map_file)]
    except Exception:  # noqa: BLE001
        pf = []
    k = f"{project.root}|{step_id}|{pf}"
    if k in _PROPS:
        return _PROPS[k]
    out = {"ci": False, "iq": False, "fit": False, "prias": False}
    try:
        with np.load(project.root / s.outputs["ebsd"].path, allow_pickle=False) as z:
            for name in ("ci", "iq", "fit"):
                if f"prop_{name}" in z.files:
                    v = z[f"prop_{name}"]
                    ok = np.isfinite(v)
                    out[name] = bool(ok.any()) and (name != "fit" or float(v[ok].min()) != float(v[ok].max()))
    except (OSError, KeyError, ValueError):
        pass
    try:
        from .io.ang import data_column_count, read_header  # noqa: PLC0415

        f = project.sample(s.params["map_id"]).map_file
        out["prias"] = read_header(f).declares_prias and max(data_column_count(f, 200)) >= 13
        from .io import prias  # noqa: PLC0415

        for k, pf in prias.available(f).items():
            if k in prias.CHANNEL_ORDER and prias.check(pf, f)["ok"]:
                out[f"prias_{k}"] = True
    except Exception:  # noqa: BLE001  (file moved, not an .ang)
        pass
    _PROPS[k] = out
    return out


ALPHA_OVERLAYS = ("hagb", "lagb", "interface", "burgers", "crack")       # from the alpha grains (alpha map tab)
BETA_OVERLAYS = ("beta_gb", "hagb", "lagb", "crack")     # parent grains of the reconstruction; HAGB / LAGB of the
                                                         # prior-beta grains (grains.calc on the parent map, fix 3.10)


def available(project: Project, map_id: str, tab: str | None = None) -> dict:
    """What the viewer can show for a map: only the layers and overlays that the loaded data and the steps run so
    far can produce (no Fit map when Fit is not stored, no PRIAS without PRIAS columns, KAM / GND only after they were
    computed, boundaries only after grains / reconstruction, crack only when a crack path is drawn). tab = 'alpha' |
    'beta' limits the overlays to that map tab (prior-beta tab: prior-beta boundaries from the parent grains)."""
    from .annotations import load_annotations  # noqa: PLC0415
    from .service import latest_step  # noqa: PLC0415

    r = [s for s in project.recipe().steps if not s.stale and s.params.get("map_id") == map_id]
    ops = {s.op for s in r}
    kinds_done = {s.params.get("kind") for s in r if s.op == "map.compute"}
    layers = []
    src = latest_step(project, ("ebsd.cleanup", "ebsd.load"), map_id=map_id)
    if src is not None:
        have = stored_data(project, src.id)
        layers += ["ipf", "phase"] + [k for k in ("ci", "iq", "fit", "prias", *PRIAS_KINDS) if have.get(k)]
        layers += [k for k in ("kam", "grod", "gos", "gnd") if k in kinds_done]
    if "recon.run" in ops:
        layers += list(RECON_KINDS)
    overlays = []
    steps = project.recipe().steps
    g_alpha, g_beta = newest_grains(steps, map_id, "alpha"), newest_grains(steps, map_id, "beta")
    if tab == "beta":
        if "recon.run" in ops:
            overlays.append("beta_gb")
        if g_beta:
            overlays += ["hagb", "lagb"]
    else:
        if g_alpha:
            overlays += ["hagb", "lagb", "interface", "burgers"]
        if "recon.run" in ops and tab is None:
            overlays.append("beta_gb")
    try:
        if any(a.type == "crack" for a in load_annotations(project, map_id)):
            overlays.append("crack")
    except Exception:  # noqa: BLE001
        pass
    if tab in ("alpha", "beta"):
        overlays = [o for o in overlays if o in (BETA_OVERLAYS if tab == "beta" else ALPHA_OVERLAYS)]
    return {"layers": layers, "overlays": overlays, "loaded": bool(ops & {"ebsd.load"}),
            "grainsize": any(s.op == "grainsize.run" for s in r), "recon": "recon.run" in ops,
            "grains_alpha": g_alpha, "grains_beta": g_beta}


def key_pngs(project: Project, map_id: str, lp: LayerParams, backend: str | None = None) -> list:
    """IPF colour key PNG(s) of the phases a layer shows (viewer legend), drawn by the backend outside the recipe
    and cached in cache/viewer/: alpha (+ beta when the map has beta) for 'ipf', beta for 'parent_ipf'."""
    from .ops_ebsd import _ipf_vector  # noqa: PLC0415

    if lp.kind not in ("ipf", "parent_ipf"):
        return []
    backend = backend or project.info().backend
    style = load_settings().figure
    sheet = project.sample(map_id)
    L = get_layer(project, map_id, lp, backend)
    keys = L.info.get("keys") or {}
    out = []
    ctx = None
    for ph, k in keys.items():
        vec = tuple(k.get("vector") or _ipf_vector(sheet, k["direction"], None))
        f = project.cache_dir / "viewer" / f"key_{json_hash([backend, ph, k['key'], vec])[7:23]}.png"
        if not f.is_file():
            ctx = ctx or view_context(project, backend)
            f.parent.mkdir(parents=True, exist_ok=True)
            ctx.backend.render_key(_any_map_handle(ctx, map_id), ph, k["key"], vec, f, min(style.dpi, 200))
        out.append(f)
    return out


def handle_alive(ctx, step_id: str) -> bool:
    """The backend object of a step exists in this session (no rebuild needed)."""
    from .service import SESSION  # noqa: PLC0415

    h = SESSION.get((str(ctx.project.root), step_id, ctx.backend.name))
    return h is not None and h.id in set(ctx.backend.handles())


def _any_map_handle(ctx, map_id: str):
    """A backend object of the map for things that need only its phases / crystal symmetries (the IPF key): one that
    is alive already (load or cleanup), else the load step, which is rebuilt much faster than a cleanup (fix 3.8: the
    key used to rebuild the newest cleanup, i.e. load + cleanup again, just to draw the key)."""
    from .service import SESSION, latest_step  # noqa: PLC0415

    steps = [s for s in ctx.project.recipe().steps if not s.stale and s.params.get("map_id") == map_id
             and s.op in ("ebsd.cleanup", "ebsd.load")]
    alive = set(ctx.backend.handles())
    for s in reversed(steps):
        h = SESSION.get((str(ctx.project.root), s.id, ctx.backend.name))
        if h is not None and h.id in alive:
            return h
    load = latest_step(ctx.project, ("ebsd.load",), map_id=map_id)
    if load is None:
        raise KeyError(f"map {map_id!r} is not loaded yet: run ebsd.load first")
    return ctx.use(load.id)
