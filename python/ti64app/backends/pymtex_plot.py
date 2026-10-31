"""pymtex renderer: figures for results computed by the pymtex backend. (Skeleton.)

Uses the shared FigureStyle from Settings (IPF key per phase, reference direction, colour maps,
line widths, scale bar, size, DPI) so the output is as close as possible to matlab/+ti64/+plot.
Overlays from Python-only modules (crack paths, positions, annotations) arrive as vector data.

pymtex API notes (docs/data_audit.md): the IPF reference direction is the `ipfDirection` argument /
attribute of the key (`ipfHKLKey(cs, ipfDirection=vector3d.Z)`); assigning the MATLAB name
`inversePoleFigureDirection` is silently ignored.
"""
from __future__ import annotations

from ..settings import FigureStyle, RefDirection

KEY_CLASS_NAMES = ("ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey")   # same names as in MTEX 7


def ipf_key(cs, style: FigureStyle, phase: str, build_vector=None):
    """Return the pymtex IPF colour key for one phase following the shared style."""
    import mtex  # noqa: PLC0415  (heavy import, only when drawing)

    s = style.ipf.get(phase) or next(iter(style.ipf.values()))
    vec = _direction(s.direction, s.custom_vector, build_vector)
    return getattr(mtex, s.key)(cs, ipfDirection=vec)


def _direction(direction: RefDirection, custom, build_vector):
    from mtex import vector3d  # noqa: PLC0415

    if direction == "X":
        return vector3d.X
    if direction == "Y":
        return vector3d.Y
    if direction == "Z":
        return vector3d.Z
    if direction == "build":
        if build_vector is None:
            raise ValueError("IPF reference = build direction, but the sample sheet has no build direction")
        return vector3d(*build_vector)
    if custom is None:
        raise ValueError("custom IPF reference direction needs custom_vector")
    return vector3d(*custom)
