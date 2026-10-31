"""pymtex renderer: figures for results computed by the pymtex backend (matplotlib via pymtex's
native hex-grid map plotting). The MATLAB backend draws the same RenderSpec with matlab/+ti64/render.m.

pymtex API notes (docs/data_audit.md): the IPF reference direction is the `ipfDirection` argument /
attribute of the key (`ipfHKLKey(cs, ipfDirection=vector3d.Z)`); assigning the MATLAB name
`inversePoleFigureDirection` is silently ignored.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import RenderSpec

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
    if spec.rgb is not None:
        rgb = np.where(np.isnan(spec.rgb), 1.0, np.clip(spec.rgb, 0, 1))
        ebsd.plot(grid_from_file_order(ebsd, rgb, perm, keep, fill=1.0), **kw)
    else:
        vals = grid_from_file_order(ebsd, spec.values.astype(float), perm, keep)
        ebsd.plot(vals, colormap=MATLAB_CMAPS.get(spec.cmap, spec.cmap), colorRange=spec.clim,
                  colorbar=bool(spec.colorbar_label), **kw)
    fig, ax = plt.gcf(), plt.gca()            # pymtex opens its own map figure
    fig.set_size_inches(w_in, w_in * aspect + (0.6 if spec.title else 0.3))
    if spec.seg is not None and len(spec.seg["x1"]):
        # pymtex draws the map in screen coordinates (pos . east, pos . north), e.g. (x, -y) for the
        # y-down convention; overlays come in map coordinates and are projected the same way
        east = np.asarray(_vec(ebsd.how2plot, "east"), float)[:2]
        north = np.asarray(_vec(ebsd.how2plot, "north"), float)[:2]

        def screen(x, y):
            p = np.stack([x, y], 1)
            return np.stack([p @ east, p @ north], 1)
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
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=spec.dpi, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return out_png


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
