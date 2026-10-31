"""Settings: machine paths (outside the repo) and the shared figure style.

Machine-specific paths are read from the first TOML file found in this order:
  1. the file named by the environment variable TI64_SETTINGS
  2. <repo>/settings.local.toml            (git-ignored)
  3. %APPDATA%/ti64app/settings.toml        (~/.config/ti64app/settings.toml elsewhere)
See settings.example.toml. Missing files are not an error; paths are then empty.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]

BackendName = Literal["pymtex", "matlab"]
IpfKeyName = Literal["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"]
RefDirection = Literal["X", "Y", "Z", "build", "custom"]


class Paths(BaseModel):
    mtex_path: str = ""
    matlab_root: str = ""
    literature_dir: str = ""
    cache_dir: str = ""


class IpfKeySetting(BaseModel):
    key: IpfKeyName = "ipfHKLKey"
    direction: RefDirection = "Z"
    custom_vector: tuple[float, float, float] | None = None
    key_in_figure: bool = True          # False: export the key as its own image


class FigureStyle(BaseModel):
    """Shared by both renderers (MATLAB +ti64/+plot and backends/pymtex_plot.py)."""
    ipf: dict[str, IpfKeySetting] = Field(
        default_factory=lambda: {"Ti (alpha)": IpfKeySetting(), "Ti (beta)": IpfKeySetting()})
    colormap_scalar: str = "parula"     # CI / IQ / fit maps
    colormap_kam: str = "turbo"
    boundary_line_width: float = 1.0
    boundary_color: str = "k"
    scale_bar: bool = True
    width_cm: float = 12.0
    dpi: int = 600


class Settings(BaseModel):
    backend: BackendName = "pymtex"
    paths: Paths = Field(default_factory=Paths)
    figure: FigureStyle = Field(default_factory=FigureStyle)


def settings_file_candidates() -> list[Path]:
    c: list[Path] = []
    if os.environ.get("TI64_SETTINGS"):
        c.append(Path(os.environ["TI64_SETTINGS"]))
    c.append(REPO_ROOT / "settings.local.toml")
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    c.append(Path(base) / "ti64app" / "settings.toml")
    return c


def find_settings_file() -> Path | None:
    return next((p for p in settings_file_candidates() if p.is_file()), None)


def load_settings(path: Path | None = None) -> Settings:
    """Load machine settings; returns defaults if no settings file exists."""
    path = path or find_settings_file()
    if path is None:
        return Settings()
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return Settings.model_validate(data)
