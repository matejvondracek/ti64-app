"""pymtex renderer: figures for results computed by the pymtex backend (matplotlib via pymtex's
native hex-grid map plotting). The MATLAB backend draws the same RenderSpec with matlab/+ti64/render.m.

pymtex API notes (docs/data_audit.md): the IPF reference direction is the `ipfDirection` argument /
attribute of the key (`ipfHKLKey(cs, ipfDirection=vector3d.Z)`); assigning the MATLAB name
`inversePoleFigureDirection` is silently ignored.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import RenderSpec, rgb_to_rgba8, value_clim

KEY_CLASS_NAMES = ("ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey")   # same names as in MTEX 7
MATLAB_CMAPS = {"parula": "viridis", "turbo": "turbo", "gray": "gray", "hsv": "hsv", "jet": "jet"}


def _arr(v):
    return np.asarray(v() if callable(v) else v)


def _vec(how2plot, name):
    v = getattr(how2plot, name)
    v = v() if callable(v) else v
    return np.asarray(v.data if hasattr(v, "data") else v, float).ravel()


def make_key(cs, key: str, vec):
    import mtex  # noqa: PLC0415

    if key not in KEY_CLASS_NAMES:
        raise ValueError(f"unknown IPF key {key!r}")
    return getattr(mtex, key)(cs, ipfDirection=mtex.vector3d(*vec))


def grid_from_file_order(ebsd, file_vals: np.ndarray, perm: np.ndarray, keep: np.ndarray, fill=np.nan):
    """Scatter file-ordered per-pixel values back onto the map grid (padding = fill)."""
    shape = (keep.size,) + file_vals.shape[1:]
    g = np.full(shape, fill, dtype=float)
    g[np.flatnonzero(keep)[perm]] = file_vals
    return g.reshape(tuple(ebsd.shape) + file_vals.shape[1:])


def _cmap(name: str):
    from matplotlib import colormaps  # noqa: PLC0415

    return colormaps[MATLAB_CMAPS.get(name, name)].resampled(256)


def measurement_colors(spec: RenderSpec) -> tuple[np.ndarray, dict]:
    """Per-measurement RGBA (uint8) as the pymtex renderer colours `spec` (base.AnalysisBackend)."""
    if spec.rgb is not None:
        return rgb_to_rgba8(spec.rgb), {"clim": None, "cmap": None, "colorbar": None}
    v = np.asarray(spec.values, float).ravel()
    lo, hi = value_clim(v, spec.clim)
    cm = _cmap(spec.cmap)
    lut = np.round(255 * cm(np.arange(256))).astype(np.uint8)          # (256, 4)
    ok = np.isfinite(v)
    k = np.round(255 * np.clip((v[ok] - lo) / (hi - lo), 0, 1)).astype(np.int64)
    out = np.zeros((v.size, 4), np.uint8)
    out[ok] = lut[k]
    out[ok, 3] = 255
    return out, {"clim": (lo, hi), "cmap": spec.cmap, "colorbar": lut}


def _screen_fn(ebsd):
    """Map coordinates -> pymtex screen coordinates (pos . east, pos . north)."""
    east = np.asarray(_vec(ebsd.how2plot, "east"), float)[:2]
    north = np.asarray(_vec(ebsd.how2plot, "north"), float)[:2]

    def screen(x, y):
        p = np.stack([np.asarray(x, float).ravel(), np.asarray(y, float).ravel()], 1)
        return np.stack([p @ east, p @ north], 1)
    return screen


def draw_prims(ax, prims: list[dict], screen) -> None:
    """Annotation primitives (base.RenderSpec.prims) in map coordinates."""
    from matplotlib.patches import Polygon  # noqa: PLC0415

    for pr in prims:
        rgb = tuple(float(c) for c in pr.get("rgb", (0, 0, 0)))
        if pr["type"] == "line":
            xy = screen(*np.asarray(pr["xy"], float).T)
            ax.plot(xy[:, 0], xy[:, 1], "-", color=rgb, lw=pr.get("width", 1.0), zorder=6,
                    solid_capstyle="butt")
        elif pr["type"] == "poly":
            xy = screen(*np.asarray(pr["xy"], float).T)
            ax.add_patch(Polygon(xy, closed=True, facecolor=rgb, edgecolor="none", zorder=6))
        elif pr["type"] == "text":
            xy = screen(*np.asarray(pr["xy"], float).reshape(2, 1))[0]
            ax.text(xy[0], xy[1], pr["text"], color=rgb, fontsize=pr.get("size", 8), zorder=7,
                    ha=pr.get("ha", "left"), va="center", clip_on=True)


def render(ebsd, spec: RenderSpec, out_png: Path, keep: np.ndarray, perm: np.ndarray) -> Path:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.collections import LineCollection  # noqa: PLC0415
    from matplotlib.lines import Line2D  # noqa: PLC0415

    plt.close("all")
    xs, ys = _arr(ebsd.x).ravel()[keep], _arr(ebsd.y).ravel()[keep]
    aspect = (ys.max() - ys.min()) / max(xs.max() - xs.min(), 1e-9)
    w_in = spec.width_cm / 2.54
    kw = {"micronbar": "on" if spec.scale_bar else "off"}
    if spec.exact:
        # exports: always pymtex's raster picture (a vector file of one polygon per cell is huge), with enough
        # pixels for the output resolution of the shown region (ROI zoom), capped at 64 M
        view_w = abs(spec.xlim[1] - spec.xlim[0]) if spec.xlim else (xs.max() - xs.min())
        out_px = spec.width_cm / 2.54 * spec.dpi * (xs.max() - xs.min()) / max(view_w, 1e-9)
        kw.update(backend="raster", maxPixels=int(min(64e6, 1.5 * out_px ** 2 * max(aspect, 0.05))))
    if spec.rgb is not None:
        rgb = np.where(np.isnan(spec.rgb), 1.0, np.clip(spec.rgb, 0, 1))
        ebsd.plot(grid_from_file_order(ebsd, rgb, perm, keep, fill=1.0), **kw)
    else:
        vals = grid_from_file_order(ebsd, spec.values.astype(float), perm, keep)
        ebsd.plot(vals, colormap=MATLAB_CMAPS.get(spec.cmap, spec.cmap), colorRange=spec.clim,
                  colorbar=bool(spec.colorbar_label), **kw)
    fig, ax = plt.gcf(), plt.gca()            # pymtex opens its own map figure
    fig.set_size_inches(w_in, w_in * aspect + (0.6 if spec.title else 0.3))
    # pymtex draws the map in screen coordinates (pos . east, pos . north), e.g. (x, -y) for the
    # y-down convention; overlays come in map coordinates and are projected the same way
    screen = _screen_fn(ebsd)
    if spec.seg is not None and len(spec.seg["x1"]):
        segs = np.stack([screen(spec.seg["x1"], spec.seg["y1"]), screen(spec.seg["x2"], spec.seg["y2"])], 1)
        lw = spec.seg_widths if spec.seg_widths is not None else spec.seg_width
        ax.add_collection(LineCollection(segs, colors=spec.seg_rgb, linewidths=lw, zorder=5))
    if spec.colorbar_label and len(fig.axes) > 1:
        fig.axes[-1].set_ylabel(spec.colorbar_label)
    if spec.legend:
        ax.legend(handles=[Line2D([0], [0], color=c, lw=2, label=l) for l, c in spec.legend],
                  loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=min(len(spec.legend), 6),
                  fontsize=7, frameon=False)
    if spec.title:
        ax.set_title(spec.title, fontsize=9)
    if spec.prims:
        draw_prims(ax, spec.prims, screen)
    if spec.xlim is not None and spec.ylim is not None:
        c = screen(np.array([spec.xlim[0], spec.xlim[1]]), np.array([spec.ylim[0], spec.ylim[1]]))
        ax.set_xlim(sorted(c[:, 0]))
        ax.set_ylim(sorted(c[:, 1]))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if spec.exact:
        _save_exact(fig, ax, spec, out_png)
    else:
        fig.savefig(out_png, dpi=spec.dpi, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out_png


SAVE_META = {".png": {"Software": None}, ".svg": {"Date": None}, ".pdf": {"CreationDate": None, "ModDate": None},
             ".tif": None, ".tiff": None}


def _save_exact(fig, ax, spec: RenderSpec, out: Path) -> None:
    """Figure exactly spec.width_cm wide (raster: round(width / 2.54 * dpi) pixels). Without title,
    colour bar and legend the map fills the figure edge to edge; otherwise the layout engine places them
    inside the fixed width. PNG / TIFF / SVG / PDF by the file extension (deterministic metadata)."""
    import matplotlib as mpl  # noqa: PLC0415

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    aspect = abs(y1 - y0) / max(abs(x1 - x0), 1e-12)
    w_in = round(spec.width_cm / 2.54 * spec.dpi) / spec.dpi       # a whole number of pixels
    bare = not spec.title and not spec.legend and len(fig.axes) == 1
    if bare:
        h_in = max(1, round(w_in * aspect * spec.dpi)) / spec.dpi
        fig.set_size_inches(w_in, h_in)
        ax.set_position([0, 0, 1, 1])
        ax.set_axis_off()
    else:
        import textwrap  # noqa: PLC0415

        cb = len(fig.axes) > 1
        lines = textwrap.wrap(spec.title, max(20, int(spec.width_cm * 7))) if spec.title else []
        if lines:
            ax.set_title("\n".join(lines), fontsize=7)
        extra = 0.16 * len(lines) + 0.1 + (0.45 if spec.legend else 0)
        map_w = w_in * (0.74 if cb else 0.96)
        if cb:
            cax = fig.axes[-1]
            cax.tick_params(labelsize=7)
            cax.yaxis.label.set_fontsize(7)
        h_in = round((map_w * aspect + extra + 0.1) * spec.dpi) / spec.dpi
        fig.set_size_inches(w_in, h_in)
        ax.set_position([0.02, (0.45 if spec.legend else 0.05) / h_in, map_w / w_in,
                         map_w * aspect / h_in])
        if cb:
            p = ax.get_position()
            cax.set_position([p.x1 + 0.015, p.y0, 0.025, p.height])
    ext = out.suffix.lower()
    kw = {"dpi": spec.dpi, "metadata": SAVE_META.get(ext)}
    if ext in (".tif", ".tiff"):
        kw["pil_kwargs"] = {"compression": "tiff_deflate"}
        kw.pop("metadata")
    with mpl.rc_context({"svg.hashsalt": "ti64", "svg.fonttype": "none"}):
        fig.savefig(out, **kw)


def render_key(cs, key: str, vec, out_png: Path, dpi: int = 300) -> Path:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    plt.close("all")
    make_key(cs, key, vec).plot()
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.gcf().savefig(out_png, dpi=dpi, bbox_inches="tight", metadata={"Software": None})
    plt.close("all")
    return out_png


def render_chart(spec, out_png: Path) -> Path:
    """Bar chart of a ChartSpec (base.py), matplotlib."""
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    w_in = spec.width_cm / 2.54
    fig, ax = plt.subplots(figsize=(w_in, 0.62 * w_in))
    h = np.asarray(spec.heights, float)
    ax.bar(np.asarray(spec.centers, float), h, width=spec.bar_width, color=spec.color, edgecolor="none")
    if spec.log_y:
        ax.set_yscale("log")
    cols = [(0.85, 0.33, 0.10), (0.10, 0.10, 0.10), (0.47, 0.67, 0.19)]     # same order as renderChart.m
    for k, (cx, cy, lab) in enumerate(spec.curves):
        ax.plot(np.asarray(cx, float), np.asarray(cy, float), "-", color=cols[k % 3], lw=1.5, label=lab)
    if spec.curves:
        ax.legend(fontsize=8, frameon=False)
    for x, lab in spec.vlines:
        ax.axvline(x, color="r", ls="--", lw=1.5)
        ax.text(x, 1.0, " " + lab, color="r", fontsize=8, va="top", ha="left",
                transform=ax.get_xaxis_transform())
    ax.set_xlabel(spec.xlabel)
    ax.set_ylabel(spec.ylabel)
    if spec.title:
        ax.set_title(spec.title, fontsize=9)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=spec.dpi, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out_png
