"""pymtex part of phase 6.7: ODF (calcDensity), pole / inverse pole figure densities, ODF export, stiffness rotation
and averaging (MTEX tensors), and the matplotlib renderers of PF / IPF figures and of E(direction).

Figure style ported from matlab_reference/XRD_part2.m (equal-area, antipodal, one colour range per figure
'setColorRange equal', turbo, sample-direction labels) and stereograph_settings.m (stereographic E plot: centre cross
of dots, bold direction labels, turbo, colour bar 'E (GPa)' with decimal commas). MATLAB: matlab/+ti64/odfBuild.m,
odfEval.m, renderTexture.m, stiffnessRotate.m, renderElastic.m.

pymtex 1.0 notes (checked 2026-11-13): odf.radon(h, r) gives the pole density in MRD for one crystal direction h and
many specimen directions r; for many crystal directions it broadcasts wrongly, so inverse pole figures are evaluated
as odf.inv().radon(r, h) (IPDF_r(h) = PDF_h(r)). Tensor rotation agrees with a numpy reference to 1e-13.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from .base import ElasticFigureSpec, OdfHandle, TextureFigureSpec

CHUNK = 200_000


def _vec(d):
    from mtex import vector3d  # noqa: PLC0415

    d = np.asarray(d, float).reshape(-1, 3)
    return vector3d(d[:, 0].copy(), d[:, 1].copy(), d[:, 2].copy())


def _miller(hkl, cs):
    from mtex import Miller  # noqa: PLC0415

    return Miller(*[float(v) for v in hkl], cs)


def _orient(quat, cs):
    from mtex import orientation, quaternion  # noqa: PLC0415

    q = np.asarray(quat, float).reshape(-1, 4)
    return orientation(quaternion(q[:, 0].copy(), q[:, 1].copy(), q[:, 2].copy(), q[:, 3].copy()), cs)


def sector_grid(sym: str, n_r: int = 41, n_phi: int = 41) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    """Crystal directions in the fundamental sector of the Laue group (hexagonal 6/mmm: azimuth 0..30 deg from x = a;
    cubic m-3m: the standard triangle 001-101-111), equal-area plot coordinates and corner labels."""
    if sym == "hexagonal":
        phi = np.linspace(0, np.pi / 6, n_phi)
        rho_max = np.full_like(phi, np.pi / 2)
        corners = [("0001", (0, 0, 1)), ("2-1-10", (1, 0, 0)), ("10-10", (np.cos(np.pi / 6), np.sin(np.pi / 6), 0))]
    else:
        phi = np.linspace(0, np.pi / 4, n_phi)
        rho_max = np.arctan(1 / np.cos(phi))
        corners = [("001", (0, 0, 1)), ("101", (1, 0, 1)), ("111", (1, 1, 1))]
    t = np.linspace(0, 1, n_r)
    R = t[:, None] * rho_max[None, :]
    P = np.broadcast_to(phi[None, :], R.shape)
    d = np.stack([np.sin(R) * np.cos(P), np.sin(R) * np.sin(P), np.cos(R)], -1)
    rr = np.sqrt(2) * np.sin(R / 2)
    return d.reshape(-1, 3), rr * np.cos(P), rr * np.sin(P), corners


def _ea(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Equal-area (Lambert) projection of upper-hemisphere unit vectors, rim at radius 1."""
    v = np.asarray(v, float).reshape(-1, 3)
    v = v * np.where(v[:, 2:3] < 0, -1, 1)                   # antipodal: fold to the upper hemisphere
    rho = np.arccos(np.clip(v[:, 2], -1, 1))
    r = np.sqrt(2) * np.sin(rho / 2)
    a = np.arctan2(v[:, 1], v[:, 0])
    return r * np.cos(a), r * np.sin(a)


class PymtexTexture:
    """Mixin of PymtexBackend (uses self._store, self._map_or_grains_ebsd)."""

    def _cs(self, handle, phase: str):
        from .pymtex_recon import phase_cs  # noqa: PLC0415

        return phase_cs(self._map_or_grains_ebsd(handle), phase)

    def _odf(self, odfh: OdfHandle):
        st = self._store.get(odfh.id)
        if st is None or "odf" not in st:
            raise KeyError(f"no pymtex ODF {odfh.id}")
        return st

    def odf_build(self, handle, phase: str, quat, weights, halfwidth_deg: float = 5.0):
        from mtex import calcDensity, degree  # noqa: PLC0415

        cs = self._cs(handle, phase)
        w = np.asarray(weights, float)
        odf = calcDensity(_orient(quat, cs), weights=w / w.sum(), halfwidth=float(halfwidth_deg) * degree)
        hid = uuid.uuid4().hex[:12]
        self._store[hid] = {"odf": odf, "cs": cs, "phase": phase,
                            "sym": "cubic" if "beta" in phase.lower() else "hexagonal"}
        mx = odf.max()
        mx = float(mx[0] if isinstance(mx, tuple) else mx)
        return OdfHandle(self.name, hid, phase), {"max_mrd": mx, "texture_index": float(odf.textureIndex()),
                                                  "n": int(len(w))}

    def odf_pf(self, odfh: OdfHandle, hkl, dirs) -> np.ndarray:
        st = self._odf(odfh)
        h = _miller(hkl, st["cs"])
        d = np.asarray(dirs, float).reshape(-1, 3)
        a = np.asarray(st["odf"].radon(h, _vec(d)), float).ravel()
        b = np.asarray(st["odf"].radon(h, _vec(-d)), float).ravel()
        return 0.5 * (a + b)                                  # antipodal (Friedel)

    def odf_ipf(self, odfh: OdfHandle, r, crystal_dirs) -> np.ndarray:
        st = self._odf(odfh)
        inv = st.get("inv")
        if inv is None:
            inv = st["inv"] = st["odf"].inv()
        H = np.asarray(crystal_dirs, float).reshape(-1, 3)
        r = np.asarray(r, float).ravel()
        a = np.asarray(inv.radon(_vec(r), _vec(H)), float).ravel()
        b = np.asarray(inv.radon(_vec(-r), _vec(H)), float).ravel()
        return 0.5 * (a + b)

    def odf_export(self, odfh: OdfHandle, path) -> Path:
        from mtex import exportODF  # noqa: PLC0415

        exportODF(self._odf(odfh)["odf"], str(path))
        return Path(path)

    def stiffness_rotate(self, handle, phase: str, quat, C66, weights=None):
        from mtex import stiffnessTensor  # noqa: PLC0415

        from ..elastic import tensor_to_voigt  # noqa: PLC0415

        cs = self._cs(handle, phase)
        C = stiffnessTensor(np.asarray(C66, float), cs)
        S = C.inv()
        q = np.asarray(quat, float).reshape(-1, 4)
        if weights is not None:
            w = np.asarray(weights, float)
            w = w / w.sum()
            accC, accS = np.zeros((3, 3, 3, 3)), np.zeros((3, 3, 3, 3))
            for k in range(0, len(q), CHUNK):
                o = _orient(q[k:k + CHUNK], cs)
                ww = w[k:k + CHUNK]
                accC += np.einsum("i,i...->...", ww, np.asarray(C.rotate(o).M))
                accS += np.einsum("i,i...->...", ww, np.asarray(S.rotate(o).M))
            return tensor_to_voigt(accC), tensor_to_voigt(accS, compliance=True)
        o = _orient(q, cs)
        Cm, Sm = np.asarray(C.rotate(o).M), np.asarray(S.rotate(o).M)
        return (np.stack([tensor_to_voigt(t) for t in Cm.reshape(-1, 3, 3, 3, 3)]),
                np.stack([tensor_to_voigt(t, compliance=True) for t in Sm.reshape(-1, 3, 3, 3, 3)]))

    # -- drawing ----------------------------------------------------------------------------------------------------
    def render_texture(self, odfh: OdfHandle, spec: TextureFigureSpec, out) -> Path:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415

        from ..texture import equal_area_grid  # noqa: PLC0415

        st = self._odf(odfh)
        n = len(spec.panels)
        vals, grids = [], []
        dens = spec.mode in ("density", "both")
        for p in spec.panels:
            if spec.kind == "pf":
                d, X, Y = equal_area_grid(46, 181)
                v = self.odf_pf(odfh, p["hkl"], d) if dens else None
                grids.append((X, Y, None))
            else:
                d, X, Y, corners = sector_grid(st["sym"])
                v = self.odf_ipf(odfh, p["vector"], d) if dens else None
                grids.append((X, Y, corners))
            vals.append(None if v is None else v.reshape(X.shape))
        lo, hi = spec.clim or (0.0, max([float(np.nanmax(v)) for v in vals if v is not None] or [1.0]))
        w_in = spec.width_cm / 2.54
        fig, axes = plt.subplots(1, n, figsize=(w_in, w_in / max(n, 1) * 1.15 + 0.4), squeeze=False,
                                 gridspec_kw={"wspace": 0.45})
        cmap = plt.get_cmap(spec.colormap)
        levels = np.linspace(lo, hi if hi > lo else lo + 1, 21)
        cs_ = None
        for k, (ax, p) in enumerate(zip(axes[0], spec.panels)):
            X, Y, corners = grids[k]
            if vals[k] is not None:
                cs_ = ax.contourf(X, Y, np.clip(vals[k], lo, hi), levels=levels, cmap=cmap)
            if spec.mode in ("scatter", "both") and spec.scatter_quat is not None:
                px, py = self._scatter_points(st, spec, p)
                ax.plot(px, py, ".", ms=0.7 if dens else spec.marker_size, color="k" if dens else "#1f3b73",
                        alpha=0.25 if dens else 0.6, mew=0)
            if spec.kind == "pf":
                t = np.linspace(0, 2 * np.pi, 361)
                ax.plot(np.cos(t), np.sin(t), "k-", lw=0.8)
                ax.plot([-1, 1], [0, 0], "k:", lw=0.5)
                ax.plot([0, 0], [-1, 1], "k:", lw=0.5)
                lab = {k: _short(v) for k, v in spec.axis_labels.items()}
                box = {"facecolor": "w", "edgecolor": "none", "pad": 0.4, "alpha": 0.85}
                # labels at the rim with a white background (XRD_part2.m: annotate(..., 'BackgroundColor', 'w'))
                ax.text(0.97, 0.0, lab.get("x", "x"), ha="right", va="center", fontsize=5.5, fontweight="bold",
                        bbox=box)
                ax.text(0.0, 0.97, lab.get("y", "y"), ha="center", va="top", fontsize=5.5, fontweight="bold",
                        bbox=box)
                ax.text(-0.04, -0.04, lab.get("z", "z"), ha="right", va="top", fontsize=5.5, bbox=box)
                ax.set_xlim(-1.1, 1.1)
                ax.set_ylim(-1.1, 1.25)
            else:
                Xb, Yb = X[-1, :], Y[-1, :]
                ax.plot(np.r_[0, Xb, 0], np.r_[0, Yb, 0], "k-", lw=0.8)
                for name, v in corners:
                    x_, y_ = _ea(np.asarray([v], float) / np.linalg.norm(v))
                    up = y_[0] > 0.05                                # the upper corner: label above it
                    ax.text(x_[0], y_[0] + (0.04 if up else -0.04), name, ha="center", va="bottom" if up else "top",
                            fontsize=7)
                ax.set_xlim(-0.15, 1.15)
                ax.set_ylim(-0.15, 0.85)
            ax.set_title(p["label"], fontsize=9)
            ax.set_aspect("equal")
            ax.axis("off")
        if cs_ is not None:
            cb = fig.colorbar(cs_, ax=list(axes[0]), fraction=0.025, pad=0.02)
            cb.set_label("MRD", fontsize=8)
            cb.ax.tick_params(labelsize=7)
        if spec.title:
            fig.suptitle(spec.title, fontsize=9)
        fig.savefig(out, dpi=spec.dpi, bbox_inches="tight", facecolor="w")
        plt.close(fig)
        return Path(out)

    def _scatter_points(self, st, spec: TextureFigureSpec, p) -> tuple[np.ndarray, np.ndarray]:
        from ..elastic import rotation_matrix  # noqa: PLC0415

        g = rotation_matrix(np.asarray(spec.scatter_quat, float))           # crystal -> frame
        if spec.kind == "pf":
            h = np.asarray(_miller(p["hkl"], st["cs"]).symmetrise().xyz, float).reshape(-1, 3)
            h = h / np.linalg.norm(h, axis=1, keepdims=True)
            v = np.einsum("nij,kj->nki", g, h).reshape(-1, 3)
            return _ea(v)
        r = np.asarray(p["vector"], float)
        r = r / np.linalg.norm(r)
        hc = np.einsum("nji,j->ni", g, r)                                  # crystal direction of r
        return _ea(_to_sector(hc, st["sym"]))

    def render_elastic(self, spec: ElasticFigureSpec, out) -> Path:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from matplotlib import colors  # noqa: PLC0415

        rho, phi = spec.rho_phi
        r = np.tan(rho / 2)                                                 # stereographic (stereograph_settings.m)
        X, Y = r * np.cos(phi), r * np.sin(phi)
        lo, hi = float(np.nanmin(spec.E)), float(np.nanmax(spec.E))
        w_in = spec.width_cm / 2.54
        fig = plt.figure(figsize=(w_in, w_in * 0.5))
        ax = fig.add_subplot(1, 2, 1)
        norm = colors.Normalize(lo, hi)
        pc = ax.pcolormesh(X, Y, spec.E, cmap=spec.colormap, norm=norm, shading="gouraud")
        t = np.linspace(-1, 1, 41)
        ax.plot(t, np.zeros_like(t), "k.", ms=1.5)
        ax.plot(np.zeros_like(t), t, "k.", ms=1.5)
        lab = spec.axis_labels
        lab = {k: _short(v) for k, v in lab.items()}
        ax.text(1.0, -0.08, lab.get("x", "x"), ha="right", va="top", fontsize=8, fontweight="bold")
        ax.text(-0.04, 1.08, lab.get("y", "y"), ha="right", va="bottom", fontsize=8, fontweight="bold")
        ax.text(-0.08, 0.08, lab.get("z", "z"), ha="right", fontsize=8, fontweight="bold", color="w")
        for name, v, E in spec.markers:
            v = np.asarray(v, float) / np.linalg.norm(v)
            v = v if v[2] >= 0 else -v
            rr = np.tan(np.arccos(np.clip(v[2], -1, 1)) / 2)
            a = np.arctan2(v[1], v[0])
            ax.plot(rr * np.cos(a), rr * np.sin(a), "wo", ms=4, mec="k", mew=0.6)
            ax.annotate(f"{name}\n{E:.1f}".replace(".", ","), (rr * np.cos(a), rr * np.sin(a)), xytext=(4, 4),
                        textcoords="offset points", fontsize=6, color="k")
        ax.set_aspect("equal")
        ax.axis("off")
        cb = fig.colorbar(pc, ax=ax, fraction=0.046, pad=0.22)
        ticks = np.linspace(lo, hi, 6)
        cb.set_ticks(ticks)
        cb.set_ticklabels([f"{x:.1f}".replace(".", ",") for x in ticks])
        cb.set_label("E (GPa)", fontsize=8)
        cb.ax.tick_params(labelsize=7)
        ax3 = fig.add_subplot(1, 2, 2, projection="3d")
        th, ph = spec.theta_phi
        E3 = spec.E_surface
        Xs, Ys, Zs = E3 * np.sin(th) * np.cos(ph), E3 * np.sin(th) * np.sin(ph), E3 * np.cos(th)
        ax3.plot_surface(Xs, Ys, Zs, facecolors=matplotlib.colormaps[spec.colormap](norm(E3)), linewidth=0, antialiased=True,
                         rstride=1, cstride=1, shade=False)
        m = float(np.nanmax(E3))
        for k, (c, lbl) in enumerate(zip(np.eye(3), ("x", "y", "z"))):
            ax3.plot(*np.stack([np.zeros(3), 1.25 * m * c]).T, "k-", lw=0.6)
            ax3.text(*(1.3 * m * c), lab.get(lbl, lbl), fontsize=6)
        ax3.set_box_aspect((1, 1, 1))
        ax3.set_axis_off()
        if spec.title:
            fig.suptitle(spec.title, fontsize=9)
        fig.savefig(out, dpi=spec.dpi, bbox_inches="tight", facecolor="w")
        plt.close(fig)
        return Path(out)


def _short(label: str) -> str:
    """'beam axis (X) = BD' -> 'X beam axis = BD' (compact axis labels on small panels)."""
    import re  # noqa: PLC0415

    m = re.match(r"(.+) \(([XYZ])\)(.*)", label)
    return f"{m.group(2)} {m.group(1)}{m.group(3)}" if m else label


def _laue_rotations(sym: str) -> np.ndarray:
    """Proper rotations of 622 (hexagonal, c = z, x = a) or 432 (cubic) as (k, 3, 3); with the antipodal fold they
    give the Laue group 6/mmm or m-3m."""
    def rz(a):
        c, s = np.cos(a), np.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    if sym == "hexagonal":
        two = np.diag([1.0, -1.0, -1.0])                     # 2-fold about x
        rots = [rz(k * np.pi / 3) for k in range(6)]
        return np.array(rots + [r @ two for r in rots])
    gens = [rz(np.pi / 2), np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]])]
    out = [np.eye(3)]
    k = 0
    while k < len(out):
        for gm in gens:
            m = np.round(gm @ out[k], 10)
            if not any(np.allclose(m, o) for o in out):
                out.append(m)
        k += 1
    return np.array(out)


def _to_sector(h: np.ndarray, sym: str) -> np.ndarray:
    """Symmetry-equivalent of each crystal direction inside the plotted fundamental sector."""
    ops = _laue_rotations(sym)
    cand = np.einsum("kij,nj->nki", ops, h)
    cand = cand * np.where(cand[..., 2:3] < 0, -1, 1)
    phi = np.mod(np.arctan2(cand[..., 1], cand[..., 0]), 2 * np.pi)
    if sym == "hexagonal":
        ok = phi <= np.pi / 6 + 1e-9
    else:
        rho = np.arccos(np.clip(cand[..., 2], -1, 1))
        ok = (phi <= np.pi / 4 + 1e-9) & (rho <= np.arctan(1 / np.maximum(np.cos(phi), 1e-9)) + 1e-9)
    idx = np.argmax(ok, axis=1)
    return cand[np.arange(len(h)), idx]
