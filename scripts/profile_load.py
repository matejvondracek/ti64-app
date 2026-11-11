"""Profile the EBSD tab path "pick a map" -> "first image visible" (fix 3.8).

Mirrors gui/ebsd_tab.py select_map() + show(): sheet / availability checks, ebsd.load as a recorded step (sample.set,
undo snapshot, output hashing, loaded-map cache), the IPF layer (backend colours, cell layout), the IPF key PNG (only
with --key: the key legend is off by default), "Compare to original" (a second layer when the map was cleaned) and
the first viewer tiles of the fitted initial view.

Usage (each mode in a FRESH process, as an app (re)start):
  python scripts/profile_load.py cold  [--map JU02] [--project DIR] [--cleanup] [--key] [--cprofile out.prof]
  python scripts/profile_load.py warm  --project DIR        (same project again: app restart)
  python scripts/profile_load.py both  [--map JU02]         (cold, then warm, then switch-back in one process)
The project goes to the scratch folder given by --project (default: projects/_profile_load, git-ignored); data/ is
only read.
"""
from __future__ import annotations

import argparse
import glob
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

ROWS: list[tuple[str, float]] = []
BACKEND = ["pymtex"]


class T:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        ROWS.append((self.name, time.perf_counter() - self.t))


def first_tiles(L, view_w: int = 1400, view_h: int = 800) -> int:
    """Render the tiles of the fitted initial view (Leaflet fitBounds, whole zoom levels)."""
    from ti64app.gui.tiles import render_layer_tile  # noqa: PLC0415

    x0, x1, y0, y1 = L.layout.extent
    base = L.layout.cw
    z = math.floor(math.log2(min((view_w - 20) * base / (x1 - x0), (view_h - 20) * base / (y1 - y0))))
    p = base / 2 ** z * 256
    n = 0
    for ty in range(math.floor(y0 / p), math.floor(y1 / p) + 1):
        for tx in range(math.floor(x0 / p), math.floor(x1 / p) + 1):
            render_layer_tile(L, z, tx, ty)
            n += 1
    return n


def pick(p, mid: str, key: bool, round_name: str) -> None:
    from ti64app import layers as lay  # noqa: PLC0415
    from ti64app import progress, service  # noqa: PLC0415
    from ti64app.settings import load_settings  # noqa: PLC0415

    ch = f"profile-{round_name}"
    with progress.channel(ch):
        with T(f"{round_name}: sheet + available"):
            p.sample(mid)
            av = lay.available(p, mid)
        if not av["loaded"]:
            with T(f"{round_name}: ebsd.load step (service.run)"):
                service.run(p, "ebsd.load", {"map_id": mid})
        with T(f"{round_name}: available x2 (tab, overlays)"):
            av = lay.available(p, mid)
            lay.available(p, mid, "alpha")
        s = load_settings().figure
        lp = lay.LayerParams(kind="ipf", ipf_keys={ph: s.ipf[ph].model_dump(mode="json")
                                                   for ph in ("Ti (alpha)", "Ti (beta)")})
        with T(f"{round_name}: IPF layer (get_layer)"):
            L = lay.get_layer(p, mid, lp)
        if key:
            with T(f"{round_name}: IPF key (key_pngs)"):
                lay.key_pngs(p, mid, lp)
        with T(f"{round_name}: first tiles"):
            n = first_tiles(L)
        load = service.latest_step(p, ("ebsd.load",), map_id=mid)
        if L.info.get("source") != load.id:
            with T(f"{round_name}: compare-to-original layer"):
                L2 = lay.get_layer(p, mid, lp.model_copy(update={"source": load.id}))
                first_tiles(L2)
    print(f"  ({round_name}: {n} tiles; progress stages: {progress.timings(ch)})")


def project(path: Path, mid_hint: str, fresh: bool, cleanup: bool):
    from ti64app import service  # noqa: PLC0415
    from ti64app.project import Project  # noqa: PLC0415

    if fresh:
        shutil.rmtree(path, ignore_errors=True)
        p = Project.create(path.name, base=path.parent, backend=BACKEND[0])
        f = glob.glob(str(REPO / "data" / "test" / f"*{mid_hint}*.ang"))[0]
        mid = p.add_files([f])[0].id
        if cleanup:                                   # a typical project: load + cleanup recorded
            service.run(p, "ebsd.load", {"map_id": mid})
            service.run(p, "ebsd.cleanup", {"map_id": mid})
            service.release_project(p)
            from ti64app import layers as lay  # noqa: PLC0415

            lay._LAYERS.clear()
            lay._LAYOUTS.clear()
    else:
        p = Project.open(path)
    mid = next(m.id for m in p.files() if mid_hint in m.id)
    return p, mid


def run_mode(mode: str, args) -> None:
    with T("import ti64app (service, layers, gui.tiles)"):
        from ti64app import layers  # noqa: F401,PLC0415
        from ti64app import service  # noqa: F401,PLC0415
        from ti64app.gui import tiles  # noqa: F401,PLC0415
    print("mtex imported at startup:", "mtex" in sys.modules)
    path = Path(args.project)
    p, mid = project(path, args.map, mode == "cold", args.cleanup)
    if mode == "cold" and args.clear_cache:
        shutil.rmtree(p.cache_dir, ignore_errors=True)
        p.cache_dir.mkdir()
    pick(p, mid, args.key, mode)
    if mode == "warm" and args.switch:
        from ti64app import layers as lay  # noqa: PLC0415

        pick(p, mid, args.key, "switch-back")
    import psutil  # noqa: PLC0415

    mi = psutil.Process().memory_info()
    if p.info().backend == "matlab":
        from ti64app.backends import get_backend  # noqa: PLC0415

        get_backend("matlab").close()
    print(f"\n{'stage':48s} {'s':>7s}")
    for n, s in ROWS:
        print(f"{n:48s} {s:7.2f}")
    tot = sum(s for n, s in ROWS if n.startswith(mode + ":"))
    print(f"{'TOTAL click -> first image (' + mode + ')':48s} {tot:7.2f}")
    print(f"peak RAM {getattr(mi, 'peak_wset', mi.rss) / 2**30:.2f} GB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["cold", "warm", "both"])
    ap.add_argument("--map", default="JU02")
    ap.add_argument("--project", default=str(REPO / "projects" / "_profile_load"))
    ap.add_argument("--cleanup", action="store_true", help="cold: record load + cleanup first (layer from cleanup)")
    ap.add_argument("--clear-cache", action="store_true")
    ap.add_argument("--key", action="store_true", help="also draw the IPF key legend")
    ap.add_argument("--no-switch", dest="switch", action="store_false")
    ap.add_argument("--cprofile")
    ap.add_argument("--backend", default="pymtex", choices=["pymtex", "matlab"], help="backend of a new project")
    args = ap.parse_args()
    BACKEND[0] = args.backend
    if args.mode == "both":
        extra = ["--project", args.project, "--map", args.map, "--backend", args.backend] + (["--key"] if args.key else []) + \
            (["--cleanup"] if args.cleanup else [])
        for m in ("cold", "warm"):
            print(f"===== {m} (fresh process) =====", flush=True)
            subprocess.run([sys.executable, __file__, m, *extra], check=True)
        return
    if args.cprofile:
        import cProfile  # noqa: PLC0415

        cProfile.runctx("run_mode(args.mode, args)", globals(), {"run_mode": run_mode, "args": args},
                        args.cprofile)
    else:
        run_mode(args.mode, args)


if __name__ == "__main__":
    main()
