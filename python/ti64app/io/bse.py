"""BSE images from the JEOL JSM-IT500HR and their .txt sidecar metadata.

JEOL writes a text file of `$KEY value...` lines next to each image (`$CM_FORMAT JEOL/SEM`).
Used keys (aliases accepted, `$`/`$$` prefixes stripped, units in values tolerated):
  CM_STAGE_POS        X Y Z [mm] tilt rotation [deg] (further numbers ignored)
  CM_ACCEL_VOLT       accelerating voltage [kV]
  CM_MAG              magnification
  SM_WD / CM_WD / *WORKING_DISTANCE   working distance [mm]
  SM_PIXEL_SIZE / CM_PIXEL_SIZE       pixel size [um] (nm if the value says nm), else
  SM_MICRON_MARKER + SM_MICRON_BAR    scale bar length [um] / [px]  -> pixel size
  CM_FULL_SIZE        width height of the full frame the scale refers to [px]
STATUS: written from the format description and tested on a synthetic file only
(docs/decisions.md D5). Check against a real JSM-IT500HR pair before trusting pixel size and stage.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from pydantic import BaseModel

IMAGE_SUFFIXES = (".tif", ".tiff", ".bmp", ".png", ".jpg", ".jpeg")

_ALIASES = {
    "stage": ("CM_STAGE_POS", "SM_STAGE_POS"),
    "kv": ("CM_ACCEL_VOLT", "SM_ACCEL_VOLT", "CM_ACCEL_VOLTAGE"),
    "mag": ("CM_MAG", "SM_MAG"),
    "wd": ("SM_WD", "CM_WD", "SM_WORKING_DISTANCE", "CM_WORKING_DISTANCE", "CM_WORK_DIST"),
    "pixel": ("SM_PIXEL_SIZE", "CM_PIXEL_SIZE", "SM_PIXEL_SIZE_UM"),
    "marker": ("SM_MICRON_MARKER", "CM_MICRON_MARKER"),
    "bar": ("SM_MICRON_BAR", "CM_MICRON_BAR"),
    "full_size": ("CM_FULL_SIZE", "SM_FULL_SIZE"),
    "instrument": ("CM_INSTRUMENT", "SM_INSTRUMENT"),
    "signal": ("CM_SIGNAL", "SM_SIGNAL"),
    "image_name": ("CM_IMAGE_NAME",),
    "date": ("CM_DATE",),
    "time": ("CM_TIME",),
}
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


class BSEMetadata(BaseModel):
    instrument: str = ""
    signal: str = ""
    image_name: str = ""
    date: str = ""
    stage_x_mm: float | None = None
    stage_y_mm: float | None = None
    stage_z_mm: float | None = None
    stage_tilt_deg: float | None = None
    stage_rotation_deg: float | None = None
    working_distance_mm: float | None = None
    accelerating_voltage_kV: float | None = None
    magnification: float | None = None
    pixel_size_um: float | None = None
    pixel_size_source: str = ""          # which keys gave the pixel size
    full_size_px: tuple[int, int] | None = None
    raw: dict[str, str] = {}
    verified_format: bool = False        # True once checked against a real JSM-IT500HR file


def parse_txt(text: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("$"):
            continue
        key, _, val = line.lstrip("$").partition(" ")
        raw[key.strip().upper()] = val.strip()
    return raw


def _first(raw: dict[str, str], what: str) -> tuple[str, str] | None:
    for k in _ALIASES[what]:
        if k in raw and raw[k] != "":
            return k, raw[k]
    return None


def _nums(s: str) -> list[float]:
    return [float(v) for v in _NUM.findall(s)]


def _length_um(s: str) -> float | None:
    v = _nums(s)
    if not v:
        return None
    s_low = s.lower()
    if "nm" in s_low:
        return v[0] / 1000.0
    if "mm" in s_low:
        return v[0] * 1000.0
    return v[0]                                  # um by default ("um", "µm" or no unit)


def read_metadata(txt: str | Path, image_shape: tuple[int, int] | None = None) -> BSEMetadata:
    """Parse a JEOL .txt. image_shape = (rows, cols) of the saved image, used to rescale the
    scale-bar pixel size when the image is not saved at CM_FULL_SIZE."""
    raw = parse_txt(Path(txt).read_text(encoding="latin-1"))
    m = BSEMetadata(raw=raw)
    for field, what in (("instrument", "instrument"), ("signal", "signal"),
                        ("image_name", "image_name"), ("date", "date")):
        hit = _first(raw, what)
        if hit:
            setattr(m, field, hit[1])
    if (hit := _first(raw, "stage")):
        v = _nums(hit[1])
        for name, val in zip(("stage_x_mm", "stage_y_mm", "stage_z_mm", "stage_tilt_deg", "stage_rotation_deg"), v):
            setattr(m, name, val)
    if (hit := _first(raw, "kv")) and (v := _nums(hit[1])):
        m.accelerating_voltage_kV = v[0] / 1000.0 if v[0] > 100 else v[0]      # > 100 -> given in V
    if (hit := _first(raw, "mag")) and (v := _nums(hit[1])):
        m.magnification = v[0]
    if (hit := _first(raw, "wd")) and (v := _nums(hit[1])):
        m.working_distance_mm = v[0] / 1000.0 if "um" in hit[1].lower() else v[0]
    if (hit := _first(raw, "full_size")):
        v = _nums(hit[1])
        if len(v) >= 2:
            m.full_size_px = (int(v[0]), int(v[1]))
    if (hit := _first(raw, "pixel")):
        m.pixel_size_um = _length_um(hit[1])
        m.pixel_size_source = hit[0]
    else:
        mk, bar = _first(raw, "marker"), _first(raw, "bar")
        if mk and bar and _nums(bar[1]):
            length = _length_um(mk[1])
            px = _nums(bar[1])[0]
            if length and px > 0:
                ps = length / px
                src = f"{mk[0]}/{bar[0]}"
                if m.full_size_px and image_shape and image_shape[1] != m.full_size_px[0]:
                    ps *= m.full_size_px[0] / image_shape[1]
                    src += f" x {m.full_size_px[0]}/{image_shape[1]} (full size / image width)"
                m.pixel_size_um = ps
                m.pixel_size_source = src
    return m


def read_image(path: str | Path) -> np.ndarray:
    """Greyscale image array (uint8 or uint16). RGB images are converted to grey (mean)."""
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile  # noqa: PLC0415

        img = tifffile.imread(path)
    else:
        from PIL import Image  # noqa: PLC0415

        img = np.asarray(Image.open(path))
    if img.ndim == 3:
        img = img[..., :3].mean(axis=2).astype(img.dtype)
    return img


def find_sidecar(image: str | Path) -> Path | None:
    image = Path(image)
    for cand in (image.with_suffix(".txt"), image.with_suffix(".TXT")):
        if cand.is_file():
            return cand
    return None


def load_bse(image: str | Path, txt: str | Path | None = None) -> tuple[np.ndarray, BSEMetadata]:
    img = read_image(image)
    txt = Path(txt) if txt else find_sidecar(image)
    if txt is None:
        raise FileNotFoundError(f"no JEOL .txt next to {image}; give the metadata file explicitly")
    return img, read_metadata(txt, image_shape=img.shape[:2])
