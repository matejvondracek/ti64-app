"""Phase 5 conformance: pymtex vs MATLAB/MTEX 7.2 for the viewer colours and exports (JU02 300x300 crop).

  measurement colours  same alpha mask and colour limits; gray (IQ) |diff| <= 1/255; turbo (KAM) |diff| <= 6/255
                       (MATLAB's turbo vs matplotlib's turbo table); parula (MATLAB) vs viridis (pymtex) not compared
  pixel-exact export   same geometry for both backends (shared ti64app.raster); phase map pixel-identical;
                       with HAGB lines < 1 % of the pixels differ (segment vertices differ slightly, phase 2)
  figure export        exact width round(mm / 25.4 * dpi) px in both renderers, bare and with decorations;
                       SVG / PDF written. Images are not compared (CLAUDE.md: conformance compares numbers).
"""
import numpy as np
import pytest
from PIL import Image

from tests.conftest import sample_params
from ti64app import service
from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams, RenderSpec

pytestmark = pytest.mark.matlab


@pytest.fixture(scope="module")
def matlab():
    be = get_backend("matlab")
    if not be.is_available():
        pytest.skip(f"matlab backend unavailable: {be.unavailable_reason()}")
    yield be
    be.close()


def test_measurement_colors(matlab, crop300_ju02):
    py = get_backend("pymtex")
    lp = LoadParams(file=crop300_ju02, input_state="oim_cleaned")
    d = py.get_ebsd(py.load_ebsd(lp))
    iq = d.props["iq"].astype(float)
    kam = np.where(d.phase > 0, np.random.default_rng(0).uniform(0, 5, d.n), np.nan)
    for values, cmap, clim, tol in ((iq, "gray", None, 1), (kam, "turbo", (0.0, 5.0), 6), (kam, "parula", None, None)):
        spec = RenderSpec(values=values, cmap=cmap, clim=clim)
        (a, ia), (b, ib) = py.measurement_colors(spec), matlab.measurement_colors(spec)
        np.testing.assert_array_equal(a[:, 3], b[:, 3])
        np.testing.assert_allclose(ia["clim"], ib["clim"], rtol=1e-12)
        assert a.shape == b.shape == (d.n, 4) and ia["colorbar"].shape == ib["colorbar"].shape == (256, 4)
        if tol is not None:
            assert np.abs(a.astype(int) - b.astype(int)).max() <= tol, cmap
    rgb = np.where(d.phase[:, None] > 0, 0.5, np.nan) * np.ones((1, 3))
    (a, _), (b, _) = py.measurement_colors(RenderSpec(rgb=rgb)), matlab.measurement_colors(RenderSpec(rgb=rgb))
    np.testing.assert_array_equal(a, b)


@pytest.fixture(scope="module")
def projects(matlab, tmp_path_factory):
    from tests.conftest import CROPS
    from ti64app.project import Project

    f = sorted(CROPS.glob("*JU02*_crop300.ang"))
    if not f:
        pytest.skip("crops missing")
    out = {}
    for be in ("pymtex", "matlab"):
        p = Project.create(be, base=tmp_path_factory.mktemp(be), backend=be)
        service.run(p, "sample.set", sample_params(f[0]))
        service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
        service.run(p, "grains.calc", {"map_id": "JU02_c300"})
        service.run(p, "annotations.set", {"map_id": "JU02_c300", "items": [
            {"map_id": "JU02_c300", "type": "arrow", "xy": [[5, 5], [20, 15]], "text": "A"},
            {"map_id": "JU02_c300", "type": "crack", "xy": [[2, 40], [30, 35], [55, 45]]}]})
        out[be] = p
    return out


def _img(p, step):
    with Image.open(p.root / step.outputs["export"].path) as im:
        return np.asarray(im.convert("RGBA"))


def test_pixel_exact_exports_same_geometry(projects):
    imgs = {}
    for be, p in projects.items():
        imgs[be] = {k: _img(p, service.run(p, "export.map", {"map_id": "JU02_c300", **params}))
                    for k, params in {"phase": {"layer": {"kind": "phase"}, "annotations": False},
                                      "phase_gb": {"layer": {"kind": "phase"}, "overlays": ["hagb", "crack"]},
                                      "ipf4": {"mode": "scale", "scale": 4, "roi": [10, 10, 30, 25]}}.items()}
    a, b = imgs["pymtex"], imgs["matlab"]
    np.testing.assert_array_equal(a["phase"], b["phase"])          # fixed phase colours + shared geometry
    # boundary segments are equivalent but their vertices differ slightly between the backends (phase 2 compares
    # counts and lengths): < 1 % of the pixels differ, all on lines
    diff = np.any(a["phase_gb"] != b["phase_gb"], axis=-1)
    assert diff.mean() < 0.01
    line = np.all(a["phase_gb"][..., :3] == 0, -1) | np.all(b["phase_gb"][..., :3] == 0, -1)
    assert (diff & ~line).sum() == 0 or (diff & ~line).mean() < 1e-4
    assert a["ipf4"].shape == b["ipf4"].shape
    assert np.abs(a["ipf4"].astype(int) - b["ipf4"].astype(int)).max() <= 1      # IPF colours |diff| <= 1e-3


@pytest.mark.parametrize("params", [
    {"mode": "width", "width_mm": 60, "dpi": 300, "layer": {"kind": "iq"}, "colorbar": False, "scale_bar": False,
     "annotations": False},
    {"mode": "width", "width_mm": 80, "dpi": 300, "overlays": ["hagb", "crack"], "title": True},
    {"mode": "width", "width_mm": 80, "dpi": 300, "layer": {"kind": "ci"}, "roi": [10, 10, 40, 30]},
    {"mode": "width", "width_mm": 80, "dpi": 300, "format": "tiff", "overlays": ["hagb"]},
], ids=["bare", "title_legend", "colorbar_roi", "tiff"])
def test_figure_export_widths(projects, params):
    for be, p in projects.items():
        s = service.run(p, "export.map", {"map_id": "JU02_c300", **params})
        with Image.open(p.root / s.outputs["export"].path) as im:
            assert im.size[0] == round(params["width_mm"] / 25.4 * params["dpi"]), be
            if params.get("colorbar") is False:
                assert abs(im.size[1] - im.size[0] * 0.866 * 52 / 60) < 0.2 * im.size[0], be


@pytest.mark.parametrize("fmt", ["svg", "pdf"])
def test_vector_exports(projects, fmt):
    for be, p in projects.items():
        s = service.run(p, "export.map", {"map_id": "JU02_c300", "mode": "width", "width_mm": 50, "dpi": 150,
                                          "format": fmt, "overlays": ["hagb"]})
        f = p.root / s.outputs["export"].path
        head = f.read_bytes()[:400]
        assert (b"<svg" in head or b"<?xml" in head) if fmt == "svg" else head.startswith(b"%PDF"), be
