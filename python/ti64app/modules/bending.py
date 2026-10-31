"""4-point-bending mechanics: stress at a map position from the sample sheet geometry and load.

Elastic beam theory in the pure-bending zone between the inner rollers:
  M = F (L_outer - L_inner) / 4,  sigma_x(y) = M y / I,  I = w t^3 / 12
Sample frame: X = beam axis, Y = thickness (neutral axis at Y = 0), Z = width. Units: mm, N, MPa.
(Stub: function signatures only.)
"""
from __future__ import annotations

from ..sample import FourPointBending


def bending_moment_Nmm(b: FourPointBending) -> float:
    raise NotImplementedError("4PB mechanics: not implemented yet")


def stress_x_MPa(b: FourPointBending, y_mm: float) -> float:
    raise NotImplementedError("4PB mechanics: not implemented yet")
