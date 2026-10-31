"""BSE images from the JEOL JSM-IT500HR and their .txt metadata (stage X/Y/Z/T/R, WD, kV, mag,
pixel size). (Stub.)"""
from __future__ import annotations

from pathlib import Path


def read_metadata(txt: str | Path) -> dict:
    """Parse the JEOL .txt sidecar. Not implemented yet (needs an example file)."""
    raise NotImplementedError("BSE metadata reader: waiting for an example JEOL .txt file")
