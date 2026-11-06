"""Service operation sem.stitch (phase 6.5): stitch overlapping SEM images into one mosaic per detector.

Python-only (modules/stitching.py), works with either backend. Outputs per detector DET (BSE / SE):
  mosaic_DET   TIFF (BigTIFF + pyramid when large), strip-free pixels + the optional info strip below
  preview_DET  PNG preview
  sidecar_DET  JSON: inputs, detector, tile positions (px, um), pixel size, stage -> mosaic transform, scores,
               residuals, info-strip height
  tiles_DET    CSV of the tile table
The mosaic + its stage transform feed phase 7 (map positions) and phase 8 (crack images, registration to the IQ map).
"""
from __future__ import annotations

from pathlib import Path

from .modules import stitching
from .service import OpContext, op


class StitchOpParams(stitching.StitchParams):
    pass


def _resolve(p: stitching.StitchParams) -> stitching.StitchParams:
    if p.folder:
        p.folder = str(Path(p.folder).absolute())
    if p.files:
        p.files = [str(Path(f).absolute()) for f in p.files]
    if not p.folder and not p.files:
        raise ValueError("give a folder or a list of image files")
    if p.normalize is None:                       # record the resolved value
        p.normalize = p.blend == "feather"
    return p


def selected_tiles(p: stitching.StitchParams, tiles: list[stitching.TileInfo]) -> list[stitching.TileInfo]:
    """Tiles the op will read: the chosen detector(s) (+ BSE when SE uses the BSE layout), minus exclusions."""
    want = {"BSE", "SE"} if p.detector == "both" else {p.detector}
    if "SE" in want and p.se_layout == "bse":
        want.add("BSE")
    return [t for t in tiles if t.detector_class in want and not stitching._match(t, p.exclude)]


@op("sem.stitch", StitchOpParams, uses_backend=False)
def _sem_stitch(ctx: OpContext) -> None:
    """Stitch overlapping SEM images (JEOL BSE / SE) of a folder or file list into one mosaic per detector."""
    p = _resolve(stitching.StitchParams.model_validate(ctx.params))
    tiles = stitching.scan(p.folder, p.files, strip_px=p.strip_px)
    for t in selected_tiles(p, tiles):
        ctx.add_input(t.path)
        if t.txt:
            ctx.add_input(t.txt)
    res = stitching.stitch(p, ctx.out_dir, tiles=tiles)
    ctx.params = p.model_dump(mode="json")
    counts = stitching.detector_counts(tiles)
    for det, r in res.items():
        ctx.output(f"mosaic_{det}", "mosaic", r.tiff, f"mosaic_{det}")
        ctx.output(f"preview_{det}", "png", r.preview, f"mosaic_{det}_preview")
        ctx.output(f"sidecar_{det}", "json", r.info, f"mosaic_{det}")
        ctx.output(f"tiles_{det}", "csv", r.table, f"tiles_{det}")
        ctx.metrics.update({f"{det}_tiles": r.info["n_tiles"], f"{det}_width_px": r.info["mosaic_px"][0],
                            f"{det}_height_px": r.info["mosaic_px"][1],
                            f"{det}_residual_max_px": r.info["residual_px"]["max"] or 0.0,
                            f"{det}_stage_only": len(r.info["stage_only_tiles"])})
    ctx.metrics.update({f"count_{k}": v for k, v in counts.items()})
