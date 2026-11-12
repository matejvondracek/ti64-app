"""BSE <-> EBSD registration (phase 8; the transform itself is a stub).

Reference image of a map (CLAUDE.md): PRIAS from the <map>_prias.txt export next to the .ang (top strip first, then
centre, bottom: the most BSE-like channel of the forward detector), else the IQ map. PRIAS / IQ values come in .ang
FILE order; reference_image() puts them on a pixel grid (brick layout, raster.py) for image registration.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Reference:
    values: np.ndarray            # per map point, .ang file order
    name: str                     # e.g. "PRIAS Top Strip" or "IQ"
    source: str                   # file it comes from (PRIAS export or the .ang)


def reference_values(project, map_id: str) -> Reference:
    """PRIAS (preferred) or IQ of a loaded map, per point in file order."""
    from ..io import prias  # noqa: PLC0415
    from ..service import latest_step  # noqa: PLC0415

    sheet = project.sample(map_id)
    files = prias.available(sheet.map_file)
    for k in (*prias.CHANNEL_ORDER, *[k for k in files if k not in prias.CHANNEL_ORDER]):
        if k in files:
            try:
                return Reference(prias.values(files[k], sheet.map_file, project.cache_dir), prias.channel(files[k])[1],
                                 str(files[k]))
            except prias.PriasMismatch:
                continue
    load = latest_step(project, ("ebsd.load",), map_id=map_id)
    if load is None:
        raise KeyError(f"map {map_id!r} is not loaded yet and has no PRIAS export: load it on the EBSD tab first")
    with np.load(project.root / load.outputs["ebsd"].path, allow_pickle=False) as z:
        return Reference(z["prop_iq"].astype(float), "IQ", sheet.map_file)


def reference_image(project, map_id: str, scale: int = 1) -> tuple[np.ndarray, Reference, dict]:
    """(float image, reference, pixel-grid meta) of the reference on the map's pixel grid (NaN = no point)."""
    from ..raster import cell_layout, exact_grid, index_image  # noqa: PLC0415
    from ..service import latest_step  # noqa: PLC0415

    ref = reference_values(project, map_id)
    load = latest_step(project, ("ebsd.load",), map_id=map_id)
    if load is None:
        raise KeyError(f"map {map_id!r} is not loaded yet: load it on the EBSD tab first")
    import json  # noqa: PLC0415

    meta = json.loads((project.root / load.outputs["meta"].path).read_text(encoding="utf-8"))
    with np.load(project.root / load.outputs["ebsd"].path, allow_pickle=False) as z:
        L = cell_layout(z["x"], z["y"], meta["grid"], meta["step_um"])
    g = exact_grid(L, scale)
    idx = index_image(L, g)
    img = np.where(idx >= 0, ref.values[np.clip(idx, 0, None)], np.nan)
    return img, ref, g.meta()


def register(bse_image, reference, **params):
    raise NotImplementedError("registration: not implemented yet (phase 8)")

