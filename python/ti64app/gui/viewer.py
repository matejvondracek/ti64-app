"""Map viewer element (Leaflet, map_viewer.js) used by the EBSD tab (gui/ebsd_tab.py): zoom / pan over tiled maps drawn
with the active backend's colours, overlays, annotations / ROIs, probe and distance measurement, hold-to-compare with
the original map, view restore."""
from __future__ import annotations

from pathlib import Path

from nicegui.element import Element

from ..annotations import Annotation, rgb_of

TOOLS = {"pan": "pan / zoom", "probe": "probe", "measure": "measure", "text": "text", "arrow": "arrow",
         "line": "line", "polyline": "polyline", "point": "point", "crack": "crack path", "roi_rect": "ROI ▭",
         "roi_poly": "ROI polygon"}


class MapViewer(Element, component="map_viewer.js"):
    """Leaflet map in um (see map_viewer.js)."""

    def __init__(self, base: float, extent: list[float]) -> None:
        super().__init__()
        import nicegui.elements.leaflet as nl  # noqa: PLC0415  (registers the 'nicegui-leaflet' ESM module)

        self.add_resource(Path(nl.__file__).parent / "dist")             # prop 'resource-path' (leaflet.css)
        self._props.update(base=base, extent=extent, layer_url="", layer_opacity=1.0, overlays=[], shapes=[],
                           tool="pan", scale_bar=True, alt_layer_url="", initial_view=None, legends=None,
                           legend_data={}, hex_grid=False, cell_um=0.0)

    def set(self, **props) -> None:
        self._props.update(props)
        self.update()


def shapes_of(items: list[Annotation]) -> list[dict]:
    out = []
    for a in items:
        r = rgb_of(a)
        out.append({"id": a.id, "type": a.type, "xy": [list(p) for p in a.xy], "text": a.text, "width_pt": a.width_pt,
                    "color": "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in r)})
    return out
