"""Profile a recipe replay: time per step split into the operation itself, writing / hashing outputs, comparing
with the recorded outputs and the recipe / undo bookkeeping.

    python scripts/profile_replay.py "projects/Test Project" --backend pymtex [--stale] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import psutil  # noqa: E402

from ti64app import history, service  # noqa: E402
from ti64app.project import Project  # noqa: E402

T: dict[str, float] = defaultdict(float)


def timed(mod, name, bucket):
    f = getattr(mod, name)

    def w(*a, **k):
        t0 = time.perf_counter()
        try:
            return f(*a, **k)
        finally:
            T[bucket] += time.perf_counter() - t0
    setattr(mod, name, w)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--stale", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    p = Project.open(Path(a.project))
    timed(service, "_check_output", "compare")
    timed(service, "_write_output", "write+hash")
    timed(history, "snapshot", "undo snapshot")
    timed(history, "commit", "undo commit")
    timed(service, "materialize", "materialize")
    rows = []
    last = {"t": time.perf_counter(), "T": dict(T)}

    def on_step(k, n, s):
        now = time.perf_counter()
        if rows:
            rows[-1]["wall_s"] = round(now - last["t"], 2)
            rows[-1].update({b: round(T[b] - last["T"].get(b, 0.0), 2) for b in T})
        last["t"], last["T"] = now, dict(T)
        rows.append({"k": k + 1, "n": n, "id": s.id, "op": s.op, "recorded_backend": s.backend})
        if len(rows) > 1:
            r = rows[-2]
            print(f"  -> wall {r['wall_s']} s, compare {r.get('compare', 0):.2f} s, write {r.get('write+hash', 0):.2f} s, "
                  f"rss {psutil.Process().memory_info().rss / 2**30:.2f} GB", flush=True)
        print(f"step {k + 1} of {n}: {s.op} ({s.backend})", flush=True)

    root = p.cache_dir / "profile_replay"
    if root.exists():
        shutil.rmtree(root)
    scratch = Project.create(root.name, base=root.parent, backend=p.info().backend)
    t0 = time.perf_counter()
    rep = service.run_recipe(scratch, p.recipe().steps, backend=a.backend, include_stale=a.stale,
                             recorded_root=p.root, on_step=on_step)
    now = time.perf_counter()
    rows[-1]["wall_s"] = round(now - last["t"], 2)
    rows[-1].update({b: round(T[b] - last["T"].get(b, 0.0), 2) for b in T})
    op_s = {r.step_id: r.seconds for r in rep.steps}
    for r in rows:
        r["op_s"] = op_s.get(r["id"])
    total = round(now - t0, 1)
    print(f"\n{'#':>3} {'op':16} {'rec':7} {'wall':>7} {'op':>7} {'write':>7} {'compare':>8} {'undo':>6}")
    for r in rows:
        print(f"{r['k']:>3} {r['op']:16} {str(r['recorded_backend']):7} {r.get('wall_s', 0):7.2f} {r['op_s'] or 0:7.2f} "
              f"{r.get('write+hash', 0):7.2f} {r.get('compare', 0):8.2f} "
              f"{r.get('undo snapshot', 0) + r.get('undo commit', 0):6.2f}")
    print(f"total {total} s; buckets: " + ", ".join(f"{k} {v:.1f} s" for k, v in T.items()))
    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "total_s": total, "buckets": dict(T)}, indent=1))
    service.release_project(scratch)
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
