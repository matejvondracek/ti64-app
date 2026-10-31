"""Full maps (slow). The JU02 reconstruction reference numbers will be checked here later."""
import numpy as np
import pytest

from ti64app.backends import get_backend
from ti64app.backends.base import LoadParams
from ti64app.io import ang

pytestmark = pytest.mark.slow


def test_full_map_header_matches_data(full_map):
    h = ang.read_header(full_map)
    n = sum(1 for line in open(full_map, encoding="latin-1") if not line.startswith("#"))
    assert n == h.n_points
    assert ang.data_column_count(full_map, n_rows=n) == {10}      # no PRIAS anywhere


@pytest.mark.parametrize("backend", ["pymtex", pytest.param("matlab", marks=pytest.mark.matlab)])
def test_full_map_load(full_map, backend):
    be = get_backend(backend)
    if not be.is_available():
        pytest.skip(be.unavailable_reason())
    h = be.load_ebsd(LoadParams(file=full_map, input_state="oim_cleaned"))
    d = be.get_ebsd(h)
    hdr = ang.read_header(full_map)
    assert d.n == h.n_points == hdr.n_points
    # file order: the first and last data rows of the .ang
    with open(full_map, encoding="latin-1") as fh:
        first = next(line for line in fh if not line.startswith("#")).split()
    assert d.x[0] == pytest.approx(float(first[3])) and d.y[0] == pytest.approx(float(first[4]))
    assert d.y[-1] == pytest.approx(d.y.max())
    assert np.isnan(d.quat[d.phase == 0]).all()
    be.release(h)
