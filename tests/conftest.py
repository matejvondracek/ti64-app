"""Shared fixtures. Data live in data/test (git-ignored); tests that need them skip when missing.

Crops are made with:  python scripts/make_crops.py   (300: unit tests, 1000: reconstruction tests)
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "test"
CROPS = DATA / "crops"
MAPS = ("JU02", "RI02", "RM02")


def _one(folder: Path, pattern: str) -> Path:
    hits = sorted(folder.glob(pattern))
    if not hits:
        pytest.skip(f"test data missing: {folder / pattern} (git-ignored; see docs/data_audit.md)")
    return hits[0]


@pytest.fixture(params=MAPS)
def crop300(request) -> Path:
    return _one(CROPS, f"*{request.param}*_crop300.ang")


@pytest.fixture
def crop300_ju02() -> Path:
    return _one(CROPS, "*JU02*_crop300.ang")


@pytest.fixture(params=MAPS)
def crop1000(request) -> Path:
    return _one(CROPS, f"*{request.param}*_crop1000.ang")


@pytest.fixture(params=MAPS)
def full_map(request) -> Path:
    return _one(DATA, f"*{request.param}*.ang")


@pytest.fixture
def up2_file() -> Path:
    return _one(DATA, "*.up2")
