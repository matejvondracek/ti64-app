"""Full maps (slow). The JU02 reconstruction reference numbers will be checked here."""
import numpy as np
import pytest

from ti64app.io import ang

pytestmark = pytest.mark.slow


def test_full_map_header_matches_data(full_map):
    h = ang.read_header(full_map)
    n = sum(1 for line in open(full_map, encoding="latin-1") if not line.startswith("#"))
    assert n == h.n_points
    assert ang.data_column_count(full_map, n_rows=n) == {10}      # no PRIAS anywhere


def test_full_map_pymtex_load(full_map):
    mtex = pytest.importorskip("mtex")
    e = mtex.loadEBSD(str(full_map), setting=2)
    pad = e.isPadding() if callable(e.isPadding) else e.isPadding
    assert int(e.size - np.sum(pad)) == ang.read_header(full_map).n_points
