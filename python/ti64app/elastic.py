"""Single-crystal elastic constants (Settings presets) and the backend-neutral tensor algebra of phase 6.7.

The backends rotate the single-crystal stiffness into the specimen frame for every orientation and average it (MTEX
tensor rotation + weighted mean; Mainprice et al. 2011, doi:10.1144/SP360.10). Here: 6x6 Voigt matrices <-> 3x3x3x3
tensors, Voigt / Reuss / Hill averages (Hill 1952, doi:10.1088/0370-1298/65/5/307), Young's modulus along a direction
E(n) = 1 / (n_i n_j n_k n_l S_ijkl), the bootstrap over grains and the isotropic (random texture) averages used by the
tests. Units GPa. Porosity is ignored (fully dense material).

Presets (user decision 2026-11-13, phase 6.7 item 7; values read from C:/Claude/literature):
- alpha default: Fisher & Renken 1964, pure alpha-Ti at 298 K, doi:10.1103/PhysRev.135.A482 (Table II; C12 = C11 -
  2 C66, C13 from Table IV).
- alpha: Rossin et al. 2023, EBM Ti64 by RUS (standard beam energy, Table 6), doi:10.1016/j.ndteint.2023.102803.
- alpha: Wielewski et al. 2017, Ti64 alpha from lattice-strain pole figures (Table 1 'Optimized' of the paper;
  Rossin 2023, ref. 36, quotes C12 / C13 swapped), doi:10.1016/j.actamat.2016.12.026.
- beta default: Ti15Mo solution-treated single crystal (Olejnak et al. 2023, Table 2), doi:10.1016/j.matdes.2023.112474,
  a stand-in: no constants of the V-rich beta of Ti64 were found in the literature folder.
- beta: LCB solution-treated single crystal (same table).
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

Symmetry = Literal["hexagonal", "cubic"]
# Voigt index pairs (0-based): 11 22 33 23 13 12
VOIGT = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))


class ElasticPreset(BaseModel):
    """Independent single-crystal stiffness constants (GPa) of one phase and where they come from."""
    phase: Literal["alpha", "beta"]
    symmetry: Symmetry
    C11: float
    C12: float
    C44: float
    C33: float | None = None          # hexagonal
    C13: float | None = None          # hexagonal
    C66: float | None = None          # hexagonal: (C11 - C12) / 2 (checked)
    source: str
    doi: str = ""
    note: str = ""

    def matrix(self) -> np.ndarray:
        """6x6 Voigt stiffness matrix (GPa) in the crystal frame (hexagonal: X||a, Z||c, as both backends load)."""
        C = np.zeros((6, 6))
        if self.symmetry == "cubic":
            C[:3, :3] = self.C12
            np.fill_diagonal(C[:3, :3], self.C11)
            C[3, 3] = C[4, 4] = C[5, 5] = self.C44
            return C
        c66 = self.C66 if self.C66 is not None else (self.C11 - self.C12) / 2
        C[0, 0] = C[1, 1] = self.C11
        C[2, 2] = self.C33
        C[0, 1] = C[1, 0] = self.C12
        C[0, 2] = C[2, 0] = C[1, 2] = C[2, 1] = self.C13
        C[3, 3] = C[4, 4] = self.C44
        C[5, 5] = c66
        return C


DEFAULT_PRESETS: dict[str, ElasticPreset] = {
    "alpha: Fisher & Renken 1964 (alpha-Ti, 298 K)": ElasticPreset(
        phase="alpha", symmetry="hexagonal", C11=162.4, C33=180.7, C12=92.0, C13=69.0, C44=46.7, C66=35.2,
        source="Fisher E.S., Renken C.J., Phys. Rev. 135 (1964) A482, Table II (298 K); C12 = C11 - 2 C66",
        doi="10.1103/PhysRev.135.A482", note="pure alpha-Ti"),
    "alpha: Rossin 2023 (EBM Ti64, RUS)": ElasticPreset(
        phase="alpha", symmetry="hexagonal", C11=178.5, C33=167.7, C12=107.8, C13=62.1, C44=48.2, C66=35.4,
        source="Rossin J. et al., NDT&E Int. 135 (2023) 102803, Table 6, standard beam energy (mean values)",
        doi="10.1016/j.ndteint.2023.102803",
        note="+- 6.8 / 10.4 / 8.0 / 3.4 / 1.4 / 1.3 GPa; C66 fitted, not (C11 - C12)/2"),
    "alpha: Wielewski 2017 (Ti64, lattice strain PF)": ElasticPreset(
        phase="alpha", symmetry="hexagonal", C11=169.0, C33=196.0, C12=89.0, C13=62.0, C44=43.0, C66=40.0,
        source="Wielewski E. et al., Acta Mater. 126 (2017) 469-480, Table 1 'Optimized'; C33 from their constraint "
               "C11 + C12 = C13 + C33, C66 = (C11 - C12) / 2",
        doi="10.1016/j.actamat.2016.12.026",
        note="point estimates, no uncertainty; Rossin 2023 (ref. 36) quotes C12 and C13 swapped (62 / 89)"),
    "beta: Ti15Mo (Olejnak 2023, stand-in)": ElasticPreset(
        phase="beta", symmetry="cubic", C11=139.4, C12=95.7, C44=44.7,
        source="Olejnak J. et al., Mater. Des. 236 (2023) 112474, Table 2, Ti15Mo solution-treated single crystal",
        doi="10.1016/j.matdes.2023.112474", note="metastable beta alloy: stand-in for the V-rich beta of Ti64"),
    "beta: LCB (Olejnak 2023, stand-in)": ElasticPreset(
        phase="beta", symmetry="cubic", C11=138.0, C12=102.2, C44=42.5,
        source="Olejnak J. et al., Mater. Des. 236 (2023) 112474, Table 2, LCB solution-treated single crystal",
        doi="10.1016/j.matdes.2023.112474", note="metastable beta alloy: stand-in for the V-rich beta of Ti64"),
}
DEFAULT_ALPHA = "alpha: Fisher & Renken 1964 (alpha-Ti, 298 K)"
DEFAULT_BETA = "beta: Ti15Mo (Olejnak 2023, stand-in)"


class ElasticSettings(BaseModel):
    """Editable presets (Settings dialog) and the default per phase."""
    presets: dict[str, ElasticPreset] = Field(default_factory=lambda: {k: v.model_copy() for k, v in
                                                                       DEFAULT_PRESETS.items()})
    default_alpha: str = DEFAULT_ALPHA
    default_beta: str = DEFAULT_BETA

    def preset(self, name: str | None, phase: str) -> ElasticPreset:
        name = name or (self.default_alpha if phase == "alpha" else self.default_beta)
        if name not in self.presets:
            raise KeyError(f"no elastic-constant preset {name!r} (Settings, section 'Elastic constants')")
        p = self.presets[name]
        if p.phase != phase:
            raise ValueError(f"preset {name!r} is for {p.phase}, not {phase}")
        return p


# ---- tensor algebra ---------------------------------------------------------------------------------------------
def voigt_to_tensor(C: np.ndarray, compliance: bool = False) -> np.ndarray:
    """6x6 Voigt matrix -> 3x3x3x3 tensor (compliance: the factors 2 / 4 of the shear terms)."""
    T = np.zeros((3, 3, 3, 3))
    for a, (i, j) in enumerate(VOIGT):
        for b, (k, l) in enumerate(VOIGT):
            f = 1.0
            if compliance:
                f = (0.5 if a >= 3 else 1.0) * (0.5 if b >= 3 else 1.0)
            v = C[a, b] * f
            for (p, q) in {(i, j), (j, i)}:
                for (r, s) in {(k, l), (l, k)}:
                    T[p, q, r, s] = v
    return T


def tensor_to_voigt(T: np.ndarray, compliance: bool = False) -> np.ndarray:
    C = np.zeros((6, 6))
    for a, (i, j) in enumerate(VOIGT):
        for b, (k, l) in enumerate(VOIGT):
            f = 1.0
            if compliance:
                f = (2.0 if a >= 3 else 1.0) * (2.0 if b >= 3 else 1.0)
            C[a, b] = T[i, j, k, l] * f
    return C


def rotation_matrix(q: np.ndarray) -> np.ndarray:
    """(…,4) quaternions a b c d (MTEX: crystal -> specimen) -> (…,3,3) matrices g with v_specimen = g v_crystal."""
    q = np.asarray(q, float)
    a, b, c, d = np.moveaxis(q, -1, 0)
    return np.stack([np.stack([a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)], -1),
                     np.stack([2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)], -1),
                     np.stack([2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d], -1)], -2)


def rotate_voigt(C: np.ndarray, q: np.ndarray, compliance: bool = False) -> np.ndarray:
    """Reference implementation (tests): C' = g C g g g g for one or many orientations, as 6x6 matrices."""
    T = voigt_to_tensor(C, compliance)
    g = rotation_matrix(q)
    Tr = np.einsum("...ip,...jq,...kr,...ls,pqrs->...ijkl", g, g, g, g, T)
    flat = Tr.reshape(-1, 3, 3, 3, 3)
    out = np.stack([tensor_to_voigt(t, compliance) for t in flat])
    return out.reshape(np.shape(q)[:-1] + (6, 6))


def youngs_modulus(C: np.ndarray, n: np.ndarray) -> np.ndarray:
    """E (GPa) along unit directions n (…,3) for a 6x6 stiffness C: 1 / (n n n n : S)."""
    S = voigt_to_tensor(np.linalg.inv(C), compliance=True)
    n = np.asarray(n, float)
    n = n / np.linalg.norm(n, axis=-1, keepdims=True)
    return 1.0 / np.einsum("...i,...j,...k,...l,ijkl->...", n, n, n, n, S)


def averages(C_voigt_mean: np.ndarray, S_mean: np.ndarray) -> dict[str, np.ndarray]:
    """Voigt = mean stiffness, Reuss = inverse of the mean compliance, Hill = their arithmetic mean (6x6, GPa)."""
    CV = 0.5 * (C_voigt_mean + C_voigt_mean.T)
    CR = np.linalg.inv(0.5 * (S_mean + S_mean.T))
    return {"voigt": CV, "reuss": CR, "hill": 0.5 * (CV + CR)}


def isotropic_moduli(C: np.ndarray) -> dict[str, float]:
    """Random-texture (isotropic) Voigt / Reuss / Hill E of a single crystal: K and G averages -> E = 9KG/(3K+G)."""
    S = np.linalg.inv(C)
    KV = (C[0, 0] + C[1, 1] + C[2, 2] + 2 * (C[0, 1] + C[1, 2] + C[0, 2])) / 9
    GV = (C[0, 0] + C[1, 1] + C[2, 2] - (C[0, 1] + C[1, 2] + C[0, 2]) + 3 * (C[3, 3] + C[4, 4] + C[5, 5])) / 15
    KR = 1 / (S[0, 0] + S[1, 1] + S[2, 2] + 2 * (S[0, 1] + S[1, 2] + S[0, 2]))
    GR = 15 / (4 * (S[0, 0] + S[1, 1] + S[2, 2]) - 4 * (S[0, 1] + S[1, 2] + S[0, 2])
               + 3 * (S[3, 3] + S[4, 4] + S[5, 5]))
    E = lambda K, G: 9 * K * G / (3 * K + G)  # noqa: E731
    KH, GH = (KV + KR) / 2, (GV + GR) / 2
    return {"voigt": E(KV, GV), "reuss": E(KR, GR), "hill": E(KH, GH)}


def mix_phases(parts: list[tuple[float, np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Phases mixed by their area fractions: [(fraction, mean C, mean S)] -> (mean C, mean S) of the polycrystal."""
    f = np.array([p[0] for p in parts], float)
    f = f / f.sum()
    return sum(fi * p[1] for fi, p in zip(f, parts)), sum(fi * p[2] for fi, p in zip(f, parts))


def bootstrap(Cg: np.ndarray, Sg: np.ndarray, w: np.ndarray, directions: np.ndarray, n_runs: int = 200,
              seed: int = 0, extra: tuple[float, np.ndarray, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """Bootstrap over grains: resample grains with replacement (weights = grain areas), average, E per direction.
    Cg, Sg: (n, 6, 6) rotated stiffness / compliance of every grain; extra = (area fraction, C, S) of another phase that
    is mixed in unchanged. Returns {model: (n_runs, n_dirs)} for voigt / reuss / hill."""
    rng = np.random.default_rng(seed)
    n = len(w)
    out = {k: np.zeros((n_runs, len(directions))) for k in ("voigt", "reuss", "hill")}
    for r in range(n_runs):
        idx = rng.integers(0, n, n)
        ww = w[idx] / w[idx].sum()
        C = np.einsum("i,ijk->jk", ww, Cg[idx])
        S = np.einsum("i,ijk->jk", ww, Sg[idx])
        if extra is not None:
            fa = 1 - extra[0]
            C, S = fa * C + extra[0] * extra[1], fa * S + extra[0] * extra[2]
        av = averages(C, S)
        for k in out:
            out[k][r] = youngs_modulus(av[k], directions)
    return out


def direction_grid(n_rho: int = 46, n_phi: int = 181) -> tuple[np.ndarray, np.ndarray]:
    """Upper-hemisphere directions on a (polar, azimuth) grid for E(direction) plots and the anisotropy ratio.
    Returns (dirs (n_rho, n_phi, 3), (rho, phi) in rad)."""
    rho = np.linspace(0, np.pi / 2, n_rho)
    phi = np.linspace(0, 2 * np.pi, n_phi)
    R, P = np.meshgrid(rho, phi, indexing="ij")
    d = np.stack([np.sin(R) * np.cos(P), np.sin(R) * np.sin(P), np.cos(R)], -1)
    return d, np.stack([R, P])


def anisotropy(C: np.ndarray) -> dict:
    """Emax / Emin over all directions (fine grid + sphere symmetry of E(n) = E(-n))."""
    d, _ = direction_grid(91, 361)
    E = youngs_modulus(C, d.reshape(-1, 3))
    i, j = int(np.argmax(E)), int(np.argmin(E))
    flat = d.reshape(-1, 3)
    return {"E_max": float(E[i]), "E_min": float(E[j]), "ratio": float(E[i] / E[j]),
            "dir_max": flat[i].round(3).tolist(), "dir_min": flat[j].round(3).tolist()}
