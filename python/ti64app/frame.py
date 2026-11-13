"""Reference frames of the loaded maps vs OIM Analysis (phase 6.6 fix 5, docs/orientation_check.md).

- Both backends load .ang files with EDAX setting 2 (decisions D1). Checked on JU04_area4 (probe point row 86,
  column 305): setting 0 reproduces the file / OIM Euler angles exactly; setting 2 = setting 0 followed by a rotation
  of 180 deg about the specimen axis (1, -1, 0)/sqrt 2:  g_app = R g_file.  So, for the OIM sample axes A1 / A2 / A3
  of the .ang Euler angles (OIM 'Setting 2' display: A1 up, A2 left, x right, y down):
      app x (right) = -A2,   app y (down) = -A1,   app z (into the screen) = -A3.
  IPF colours of v and -v are equal (Laue group), so app IPF-X = OIM IPF along A2, IPF-Y = A1, IPF-Z = A3 ([001]).
- Crystal frame: the .ang header says 'CartesianAlignment a || x, c* || z'; MTEX 7.2 and pymtex load .ang
  hexagonal phases with X||a (EDAX convention). A file that says anything else (e.g. a* || x) would put every
  hexagonal orientation 30 deg about c off -> warned at load.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# rotation (quaternion a b c d) from the file (setting 0) frame to the app frame, per EDAX setting, as both backends
# apply it (measured: q_setting / q_setting0, constant over the map)
_S = np.sqrt(0.5)
SETTING_ROT = {0: (1.0, 0.0, 0.0, 0.0), 2: (0.0, _S, -_S, 0.0)}
# app axis -> OIM sample axis (sign: the app axis equals this OIM axis times the sign)
OIM_AXES = {2: {"x": ("A2", -1), "y": ("A1", -1), "z": ("A3", -1)}}
EXPECTED_ALIGNMENT = "a || x, c* || z"


def euler2quat(phi1, Phi, phi2) -> np.ndarray:
    """Bunge Euler angles (rad) -> quaternion (a, b, c, d), MTEX convention (crystal -> specimen)."""
    phi1, Phi, phi2 = (np.asarray(v, float) for v in (phi1, Phi, phi2))
    s, d = (phi1 + phi2) / 2, (phi1 - phi2) / 2
    return np.stack([np.cos(Phi / 2) * np.cos(s), np.sin(Phi / 2) * np.cos(d), np.sin(Phi / 2) * np.sin(d),
                     np.cos(Phi / 2) * np.sin(s)], axis=-1)


def quat2euler(q) -> np.ndarray:
    """Quaternion (a, b, c, d) -> Bunge Euler angles (rad, phi1 and phi2 in [0, 2 pi)); inverse of euler2quat."""
    q = np.asarray(q, float)
    a, b, c, d = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    Phi = 2 * np.arctan2(np.hypot(b, c), np.hypot(a, d))
    s, dd = np.arctan2(d, a), np.arctan2(c, b)
    return np.stack([np.mod(s + dd, 2 * np.pi), Phi, np.mod(s - dd, 2 * np.pi)], axis=-1)


def qmul(p, q) -> np.ndarray:
    p, q = np.asarray(p, float), np.asarray(q, float)
    a1, b1, c1, d1 = np.moveaxis(p, -1, 0)
    a2, b2, c2, d2 = np.moveaxis(q, -1, 0)
    return np.stack([a1 * a2 - b1 * b2 - c1 * c2 - d1 * d2, a1 * b2 + b1 * a2 + c1 * d2 - d1 * c2,
                     a1 * c2 - b1 * d2 + c1 * a2 + d1 * b2, a1 * d2 + b1 * c2 - c1 * b2 + d1 * a2], axis=-1)


def app_quat_from_file_euler(euler_rad, setting: int = 2) -> np.ndarray:
    """The orientation the backends hold for a row of the .ang (Euler angles as in the file) at this setting."""
    return qmul(np.asarray(SETTING_ROT[setting]), euler2quat(*np.moveaxis(np.asarray(euler_rad, float), -1, 0)))


def rotation_angle_deg(p, q) -> np.ndarray:
    """Angle between two rotations (no crystal symmetry), deg."""
    dot = np.abs(np.sum(np.asarray(p, float) * np.asarray(q, float), axis=-1)).clip(0, 1)
    return np.degrees(2 * np.arccos(dot))


def axis_text(setting: int = 2) -> dict[str, str]:
    """Tooltip text per app axis: which OIM sample axis it is."""
    ax = OIM_AXES.get(setting)
    if not ax:
        return {}
    return {k: f"app {k} = {'-' if s < 0 else ''}{a} of OIM (same IPF colours as {a})" for k, (a, s) in ax.items()}


def check_alignment(header_value: str | None) -> str | None:
    """Warning text if an .ang's CartesianAlignment is not the EDAX 'a || x, c* || z' the backends assume."""
    if not header_value:
        return None
    norm = re.sub(r"\s+", " ", header_value.replace("\t", " ")).strip().lower()
    if norm == EXPECTED_ALIGNMENT:
        return None
    return (f"the .ang says 'CartesianAlignment {header_value.strip()}', but both backends load hexagonal phases with "
            "X||a ('a || x, c* || z'): hexagonal orientations may be 30° about c off. Check the IPF map against OIM.")


def file_euler(ang: str | Path, cache_dir: Path | None = None) -> np.ndarray:
    """(n, 3) Euler angles (rad) of every data row of an .ang exactly as in the file (FILE order), cached as .npy
    in cache_dir/euler/ (keyed by size + mtime). The probe shows them in degrees like the OIM status bar."""
    from .hashing import file_fingerprint  # noqa: PLC0415

    ang = Path(ang)
    f = None
    if cache_dir is not None:
        key = re.sub(r"[^0-9a-zA-Z]", "", file_fingerprint(ang))[-24:]
        f = Path(cache_dir) / "euler" / f"{ang.stem[:40]}_{key}.npy"
        if f.is_file():
            return np.load(f)
    import pandas as pd  # noqa: PLC0415

    e = pd.read_csv(ang, comment="#", sep=r"\s+", header=None, usecols=[0, 1, 2], dtype=float,
                    engine="c").to_numpy()
    if f is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp.npy")
            np.save(tmp, e)
            tmp.replace(f)
        except OSError:
            pass
    return e
