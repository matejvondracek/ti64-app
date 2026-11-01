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

from .backends import get_backend
from .backends.base import RenderSpec
from .hashing import json_hash
from .project import Project
from .raster import CellLayout, SegmentIndex, cell_layout
from .recipe import RecipeStep
from .settings import IpfKeySetting, load_settings

LayerKind = Literal["ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd",
                    "parent_ipf", "recon_fit", "variant", "packet"]
MAP_KINDS = ("ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd")
RECON_KINDS = ("parent_ipf", "recon_fit", "variant", "packet")
DATA_OPS = ("ebsd.load", "ebsd.cleanup", "grains.calc", "map.compute", "recon.run")   # steps a layer depends on
OVERLAYS = ("hagb", "lagb", "interface", "burgers", "beta_gb", "crack")
LAYER_LABELS = {"ipf": "IPF (alpha / beta)", "phase": "phase", "ci": "CI", "iq": "IQ", "fit": "Fit", "prias": "PRIAS",
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
        gcol = ctx.backend.ipf_colors_quat(handle(), BETA, rd.grain["mean_quat"], key, vec)
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
        s = project.recipe().step(step_id)
        with np.load(project.root / s.outputs[output].path, allow_pickle=False) as z:
            x, y = z["x"], z["y"]
        _LAYOUTS[k] = cell_layout(x, y, _grid_of(project, map_id))
    return _LAYOUTS[k]


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(255 * float(v))) for v in c[:3])


def _cache_key(project: Project, map_id: str, lp: LayerParams, backend: str, extra: Any = None) -> str:
    r = project.recipe()
    steps = [(s.id, s.stale) for s in r.steps if s.params.get("map_id") == map_id and s.op in DATA_OPS]
    style = load_settings().figure.model_dump(mode="json")
    sheet = project.sample(map_id).model_dump(mode="json")
    return json_hash([map_id, lp.model_dump(mode="json"), backend, steps, style, sheet, extra])[7:31]


def get_layer(project: Project, map_id: str, lp: LayerParams, backend: str | None = None) -> Layer:
    """Coloured layer (cached in memory and in cache/viewer/)."""
    backend = backend or project.info().backend
    key = _cache_key(project, map_id, lp, backend)
    with _LOCK:
        if key in _LAYERS:
            return _LAYERS[key]
    f = project.cache_dir / "viewer" / f"layer_{key}.npz"
    if f.is_file():
        with np.load(f, allow_pickle=False) as z:
            colors = z["colors"]
            info = json.loads(str(z["info"]))
        L = Layer(key, colors, layout_for(project, map_id, info["source"]), info, info.get("meta", {}))
    else:
        ctx = view_context(project, backend)
        spec, _h, meta, src = layer_spec(ctx, map_id, lp)
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
    return L


def get_overlay(project: Project, map_id: str, name: str, lp: LayerParams, backend: str | None = None) -> Overlay:
    """One overlay class as a binned segment index (crack paths are re-read on every change)."""
    from .annotations import load_annotations  # noqa: PLC0415
    from .ops_ebsd import overlay_segments  # noqa: PLC0415

    backend = backend or project.info().backend
    extra = [name] + ([a.model_dump(mode="json") for a in load_annotations(project, map_id) if a.type == "crack"]
                      if name == "crack" else [])
    key = _cache_key(project, map_id, LayerParams(grains=lp.grains, recon=lp.recon), backend, extra)
    with _LOCK:
        if key in _OVERLAYS:
            return _OVERLAYS[key]
    ctx = view_context(project, backend)
    lw = load_settings().figure.boundary_line_width
    ov = overlay_segments(ctx, map_id, [name], lp.grains, lp.recon, lw)
    seg = ov["seg"] or {k: np.zeros(0) for k in ("x1", "y1", "x2", "y2")}
    O = Overlay(key, SegmentIndex(seg, ov["rgb"]), float(ov["widths"][0]) / lw if len(ov["widths"]) else 1.0,
                [(l, _hex(c)) for l, c in ov["legend"]])
    with _LOCK:
        _OVERLAYS[key] = O
    return O


def available(project: Project, map_id: str) -> dict:
    """What the viewer can show for a map: layer kinds and overlays with the steps they need."""
    r = [s for s in project.recipe().steps if not s.stale and s.params.get("map_id") == map_id]
    ops = {s.op for s in r}
    kinds_done = {s.params.get("kind") for s in r if s.op == "map.compute"}
    layers = []
    if ops & {"ebsd.load", "ebsd.cleanup"}:
        layers += ["ipf", "phase", "ci", "iq", "fit", "prias"]
        layers += [k for k in ("kam", "grod", "gos", "gnd") if k in kinds_done]
    if "recon.run" in ops:
        layers += list(RECON_KINDS)
    overlays = []
    if "grains.calc" in ops:
        overlays += ["hagb", "lagb", "interface", "burgers"]
    if "recon.run" in ops:
        overlays.append("beta_gb")
    overlays.append("crack")
    return {"layers": layers, "overlays": overlays, "loaded": bool(ops & {"ebsd.load"})}
