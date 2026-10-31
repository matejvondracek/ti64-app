"""Cut centre crops (default 300 x 300 and 1000 x 1000 points) from each hex-grid .ang map.

Usage:  python scripts/make_crops.py [data/test] [--sizes 300 1000]
Writes data/test/crops/<stem>_crop<N>.ang plus <stem>_crop<N>.json (source, offsets) for every
.ang in the folder. 300: unit tests (~60 x 52 um, smaller than one prior-beta grain);
1000: reconstruction tests (~200 x 173 um, contains whole prior-beta grains). The crop keeps the original header (only NCOLS_ODD/NCOLS_EVEN/NROWS change),
starts on an odd (OIM 1-based) row so the odd/even shift is unchanged, and shifts x, y to start at 0.
data/ is git-ignored: crops are never committed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

FMT = "%9.5f %9.5f %9.5f %12.5f %12.5f %7.1f %6.3f %2d %6d %7.3f"


def crop(path: Path, size: int, outdir: Path) -> dict:
    with open(path, "r", encoding="latin-1") as fh:
        header = []
        for line in fh:
            if not line.startswith("#"):
                break
            header.append(line)
    hdr = "".join(header)
    get = lambda k: int(re.search(rf"#\s*{k}:\s*(\d+)", hdr).group(1))  # noqa: E731
    n_odd, n_even, n_rows = get("NCOLS_ODD"), get("NCOLS_EVEN"), get("NROWS")
    if "HexGrid" not in hdr:
        raise ValueError(f"{path.name}: only HexGrid maps are handled")
    d = np.loadtxt(path, comments="#", usecols=range(10), dtype=np.float64, ndmin=2)
    # row r (0-based) has n_odd points if r is even (OIM 'odd' row), n_even otherwise
    lens = np.where(np.arange(n_rows) % 2 == 0, n_odd, n_even)
    starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
    assert starts[-1] + lens[-1] == d.shape[0], "row structure does not match the header"

    r0 = (n_rows - size) // 2
    r0 -= r0 % 2                       # start on an OIM 'odd' row
    c0 = (n_even - size) // 2
    rows = [d[starts[r] + c0: starts[r] + c0 + size] for r in range(r0, r0 + size)]
    out = np.concatenate(rows)
    x0, y0 = out[0, 3], out[0, 4]
    out[:, 3] = np.round(out[:, 3] - x0, 5)
    out[:, 4] = np.round(out[:, 4] - y0, 5)

    new_hdr = re.sub(r"(#\s*NCOLS_ODD:\s*)\d+", rf"\g<1>{size}", hdr)
    new_hdr = re.sub(r"(#\s*NCOLS_EVEN:\s*)\d+", rf"\g<1>{size}", new_hdr)
    new_hdr = re.sub(r"(#\s*NROWS:\s*)\d+", rf"\g<1>{size}", new_hdr)
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / f"{path.stem}_crop{size}.ang"
    with open(dst, "w", encoding="latin-1", newline="\n") as fh:
        fh.write(new_hdr)
        np.savetxt(fh, out, fmt=FMT)
    meta = {"source": path.name, "crop": dst.name, "rows": [int(r0), int(r0 + size)],
            "cols": [int(c0), int(c0 + size)], "x0_um": float(x0), "y0_um": float(y0),
            "n_points": int(out.shape[0]), "size": size, "rule": "map centre, start on even 0-based row"}
    dst.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="data/test")
    ap.add_argument("--sizes", type=int, nargs="+", default=[300, 1000],
                    help="crop sizes in hex rows/columns (300: unit tests, 1000: reconstruction tests)")
    a = ap.parse_args()
    folder = Path(a.folder)
    for f in sorted(folder.glob("*.ang")):
        for size in a.sizes:
            print(json.dumps(crop(f, size, folder / "crops")))
