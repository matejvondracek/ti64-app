"""Settings: machine paths (outside the repo) and the shared figure style.

Machine-specific paths are read from the first TOML file found in this order:
  1. the file named by the environment variable TI64_SETTINGS
  2. <repo>/settings.local.toml            (git-ignored)
  3. %APPDATA%/ti64app/settings.toml        (~/.config/ti64app/settings.toml elsewhere)
See settings.example.toml. Missing files are not an error; paths are then empty.
"""
from __future__ import annotations

import json
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


class FeedbackSettings(BaseModel):
    """Feedback button (fix 3.2): where GitHub issues go (the e-mail option was removed in fix 4; an 'email' key in
    old preference files is ignored)."""
    repo_url: str = "https://github.com/matejvondracek/ti64-app"


class Settings(BaseModel):
    backend: BackendName = "pymtex"
    paths: Paths = Field(default_factory=Paths)
    figure: FigureStyle = Field(default_factory=FigureStyle)
    feedback: FeedbackSettings = Field(default_factory=FeedbackSettings)


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


def preferences_path() -> Path:
    """User preferences written by the Settings page (default backend, figure style), JSON.
    TI64_PREFS overrides the location (tests)."""
    if os.environ.get("TI64_PREFS"):
        return Path(os.environ["TI64_PREFS"])
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "ti64app" / "preferences.json"


def load_settings(path: Path | None = None) -> Settings:
    """Machine paths from the TOML settings file + preferences saved by the Settings page.
    Returns defaults if neither exists."""
    path = path or find_settings_file()
    data: dict = {}
    if path is not None:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    prefs = preferences_path()
    if prefs.is_file():
        p = json.loads(prefs.read_text(encoding="utf-8"))
        data.update({k: v for k, v in p.items() if k in ("backend", "figure", "feedback")})
        # paths edited on the Settings page override the TOML file, field by field (empty = keep TOML)
        data["paths"] = {**data.get("paths", {}), **{k: v for k, v in p.get("paths", {}).items() if v}}
    return Settings.model_validate(data)


def save_preferences(settings: Settings, paths: dict[str, str] | None = None) -> Path:
    """Save the user-editable part (backend, figure style). Machine paths stay in the TOML file unless
    `paths` is given (Settings page): those fields are stored here and override the TOML file."""
    p = preferences_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d = settings.model_dump(mode="json", include={"backend", "figure", "feedback"})
    d["paths"] = old.get("paths", {}) if paths is None else {k: v for k, v in paths.items() if k in Paths.model_fields}
    tmp = p.with_name(f".{p.name}.tmp")                 # atomic: temp file + rename
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def pictures_dir() -> Path:
    """The user's Pictures folder (Windows known folder FOLDERID_Pictures, so OneDrive redirection is followed);
    ~/Pictures elsewhere or if the shell call fails; the home folder if that does not exist. Default export folder
    (fix 3.3)."""
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415
            import uuid  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", wintypes.BYTE * 8)]

            u = uuid.UUID("{33E28130-4E1E-4676-835A-98395C3BC3BB}")          # FOLDERID_Pictures
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (wintypes.BYTE * 8).from_buffer_copy(u.bytes[8:]))
            out = ctypes.c_wchar_p()
            shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ole32.CoTaskMemFree(out)
                if path.is_dir():
                    return path
        except Exception:  # noqa: BLE001
            pass
    p = Path.home() / "Pictures"
    return p if p.is_dir() else Path.home()


def logs_dir() -> Path:
    """GUI log files (the desktop app has no console): %APPDATA%/ti64app/logs (next to the preferences)."""
    return preferences_path().parent / "logs"
