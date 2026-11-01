"""Annotations and regions of interest drawn on a map (viewer), stored per project in annotations.json.

Every change is a recipe step (`annotations.set`, one map at a time), so exports that use them can be
replayed and undo/redo restores them. Coordinates are map coordinates in um (x right, y down), the
same as the EBSD data, so annotations stay where they are whatever the zoom, crop or export size.

Types: text, arrow (tail -> head), line, polyline, point (with optional label), crack (polyline; shown
with the 'crack' overlay, later also produced by modules/crack.py), roi_rect (two corners), roi_poly.
to_primitives() turns them into backend-neutral drawing primitives (RenderSpec.prims) that both
renderers draw as vectors; rasterize_primitives() draws the same primitives into pixel-exact images.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .project import Project, write_json

AnnotationType = Literal["text", "arrow", "line", "polyline", "point", "crack", "roi_rect", "roi_poly"]
ROI_TYPES = ("roi_rect", "roi_poly")
DEFAULT_COLOR = {"crack": "#e00000", "roi_rect": "#00a0ff", "roi_poly": "#00a0ff"}


class Annotation(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    map_id: str
    type: AnnotationType
    xy: list[tuple[float, float]]             # um, map coordinates
    text: str = ""                            # text / point label / ROI or crack name
    color: str = ""                           # '#rrggbb'; '' = default for the type (black otherwise)
    width_pt: float = 1.5                     # line width in figure exports (points)
    size_pt: float = 9.0                      # font size (points)
    export: bool = True                       # draw in exports


class AnnotationSet(BaseModel):
    items: list[Annotation] = Field(default_factory=list)


def annotations_path(project: Project) -> Path:
    return project.root / "annotations.json"


def load_annotations(project: Project, map_id: str | None = None) -> list[Annotation]:
    f = annotations_path(project)
    items = AnnotationSet.model_validate_json(f.read_text(encoding="utf-8")).items if f.is_file() else []
    return [a for a in items if map_id is None or a.map_id == map_id]


def save_map_annotations(project: Project, map_id: str, items: list[Annotation]) -> None:
    """Replace the annotations of one map (others unchanged)."""
    keep = [a for a in load_annotations(project) if a.map_id != map_id]
    for a in items:
        if a.map_id != map_id:
            raise ValueError(f"annotation {a.id} belongs to {a.map_id}, not {map_id}")
    write_json(annotations_path(project), AnnotationSet(items=keep + list(items)).model_dump(mode="json"))


def validate(a: Annotation) -> None:
    need = {"text": 1, "point": 1, "arrow": 2, "line": 2, "roi_rect": 2, "polyline": 2, "crack": 2, "roi_poly": 3}[a.type]
    if len(a.xy) < need:
        raise ValueError(f"{a.type} needs at least {need} points, got {len(a.xy)}")
    if a.type in ("arrow", "line", "roi_rect") and len(a.xy) != 2:
        raise ValueError(f"{a.type} needs exactly 2 points")
    if a.type == "text" and not a.text:
        raise ValueError("text annotation without text")


def rgb_of(a: Annotation) -> tuple[float, float, float]:
    c = (a.color or DEFAULT_COLOR.get(a.type, "#000000")).lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def roi_box(a: Annotation) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) bounding box of an ROI (or any annotation)."""
    xy = np.asarray(a.xy, float)
    return float(xy[:, 0].min()), float(xy[:, 1].min()), float(xy[:, 0].max()), float(xy[:, 1].max())


def find(project: Project, ann_id: str) -> Annotation:
    for a in load_annotations(project):
        if a.id == ann_id or (a.type in ROI_TYPES and a.text == ann_id):
            return a
    raise KeyError(f"no annotation or ROI {ann_id!r}")


# ---- drawing primitives ------------------------------------------------------------------------------
def _arrow_head(p0, p1, length: float, width: float) -> np.ndarray:
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    n = np.hypot(*d)
    if n == 0:
        return np.array([p1, p1, p1])
    d /= n
    perp = np.array([-d[1], d[0]])
    base = p1 - d * length
    return np.array([p1, base + perp * width / 2, base - perp * width / 2])


def _circle(c, r: float, k: int = 24) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, k, endpoint=False)
    return np.stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)], 1)


def to_primitives(items: list[Annotation], view_width_um: float, kinds: tuple[str, ...] | None = None,
                  include_roi: bool = False) -> list[dict]:
    """Annotations -> RenderSpec.prims (map coordinates). Arrow heads and point markers are sized from
    the width of the exported view, so they look the same in a full-map and an ROI export."""
    prims: list[dict] = []
    head = 0.025 * view_width_um
    for a in items:
        if not a.export or (kinds is not None and a.type not in kinds):
            continue
        if a.type in ROI_TYPES and not include_roi:
            continue
        rgb = rgb_of(a)
        xy = np.asarray(a.xy, float)
        if a.type == "text":
            prims.append({"type": "text", "xy": tuple(xy[0]), "text": a.text, "rgb": rgb, "size": a.size_pt})
        elif a.type == "point":
            prims.append({"type": "poly", "xy": _circle(xy[0], 0.006 * view_width_um), "rgb": rgb})
            if a.text:
                prims.append({"type": "text", "xy": (xy[0, 0] + 0.012 * view_width_um, xy[0, 1]), "text": a.text,
                              "rgb": rgb, "size": a.size_pt})
        elif a.type == "arrow":
            hl = min(head, 0.45 * float(np.hypot(*(xy[1] - xy[0]))))
            tip = _arrow_head(xy[0], xy[1], hl, 0.6 * hl)
            shaft_end = (tip[1] + tip[2]) / 2
            prims.append({"type": "line", "xy": np.array([xy[0], shaft_end]), "rgb": rgb, "width": a.width_pt})
            prims.append({"type": "poly", "xy": tip, "rgb": rgb})
            if a.text:
                prims.append({"type": "text", "xy": tuple(xy[0]), "text": a.text, "rgb": rgb, "size": a.size_pt,
                              "ha": "right"})
        elif a.type == "roi_rect":
            (x0, y0), (x1, y1) = xy
            prims.append({"type": "line", "xy": np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]),
                          "rgb": rgb, "width": a.width_pt})
        elif a.type == "roi_poly":
            prims.append({"type": "line", "xy": np.vstack([xy, xy[:1]]), "rgb": rgb, "width": a.width_pt})
        else:                                    # line, polyline, crack
            prims.append({"type": "line", "xy": xy, "rgb": rgb, "width": a.width_pt})
    return prims


def rasterize_primitives(img: np.ndarray, grid, prims: list[dict], line_px: float) -> list[str]:
    """Draw line and polygon primitives into a pixel-exact image (raster.PixelGrid). Texts cannot be drawn
    without a font renderer: they are skipped and reported (use a width-mode export for labels)."""
    from matplotlib.path import Path as MplPath  # noqa: PLC0415

    from .raster import rasterize_segments  # noqa: PLC0415

    skipped = []
    for pr in prims:
        if pr["type"] == "line":
            xy = np.asarray(pr["xy"], float)
            px, py = grid.to_px(xy[:, 0], xy[:, 1])
            rasterize_segments(img, px[:-1], py[:-1], px[1:], py[1:], pr["rgb"], line_px)
        elif pr["type"] == "poly":
            xy = np.asarray(pr["xy"], float)
            px, py = grid.to_px(xy[:, 0], xy[:, 1])
            i0, i1 = max(0, int(np.floor(py.min()))), min(img.shape[0], int(np.ceil(py.max())) + 1)
            j0, j1 = max(0, int(np.floor(px.min()))), min(img.shape[1], int(np.ceil(px.max())) + 1)
            if i1 <= i0 or j1 <= j0:
                continue
            jj, ii = np.meshgrid(np.arange(j0, j1) + 0.5, np.arange(i0, i1) + 0.5)
            inside = MplPath(np.stack([px, py], 1)).contains_points(np.stack([jj.ravel(), ii.ravel()], 1))
            inside = inside.reshape(ii.shape)
            sub = img[i0:i1, j0:j1]
            sub[inside, :3] = np.round(255 * np.asarray(pr["rgb"], float)).astype(np.uint8)
            sub[inside, 3] = 255
        elif pr["type"] == "text":
            skipped.append(pr["text"])
    return skipped
