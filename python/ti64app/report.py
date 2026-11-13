"""'Save report for Claude' (phase 6.6 fix 4): ONE self-contained Markdown file + a JSON twin in
<repo>/reports/YYYY-MM-DD_HHMM_<category>.md (git-ignored, inside the repository folder so Claude Code may read it;
CLAUDE.md tells Claude to read the newest report when the user mentions one).

Content, in this order: the user's description; app version and git commit; backend and versions; OS, RAM, memory
use; current project, tab, file, map tab; the last 30 UI actions (breadcrumbs.py); recipe steps with status and
timings; every error since start with its full traceback; the last 200 log lines; relevant settings; an optional
screenshot saved next to it (same name, .png). Absolute paths are shortened to file names (feedback.shorten_paths);
no measurement data (no arrays, no images of maps unless the user ticks the screenshot).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from . import __version__
from . import breadcrumbs as bc
from .feedback import CATEGORIES, log_tail, shorten_paths
from .settings import REPO_ROOT

REPORTS_DIR = REPO_ROOT / "reports"
N_ACTIONS, N_LOG = 30, 200


def git_commit() -> str:
    """'<short hash>' (+ ', N uncommitted changes') of the app's repository, or 'unknown'."""
    try:
        kw = {"cwd": str(REPO_ROOT), "capture_output": True, "text": True, "timeout": 5}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000            # CREATE_NO_WINDOW
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], **kw).stdout.strip()   # noqa: S603, S607
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], **kw).stdout   # noqa: S603, S607
        n = len([ln for ln in st.splitlines() if ln.strip()])
        return (h or "unknown") + (f", {n} uncommitted change(s)" if n else "")
    except Exception:  # noqa: BLE001
        return "unknown"


def backend_info(project) -> dict:
    """Active backend + versions; the MATLAB engine is never started just for the report."""
    from .backends import _instances, backend_status  # noqa: PLC0415
    from .settings import load_settings  # noqa: PLC0415

    name = project.info().backend if project is not None else load_settings().backend
    out: dict = {"active": name}
    try:
        out["available"] = {k: (f"unavailable: {v}" if v else "available") for k, v in backend_status().items()}
    except Exception as e:  # noqa: BLE001
        out["available"] = f"unknown ({e})"
    for k, be in dict(_instances).items():
        try:
            if k == "matlab" and not getattr(be, "running", False):
                out[k] = "engine not running"
                continue
            out[k] = be.versions()
        except Exception as e:  # noqa: BLE001
            out[k] = f"versions not available ({type(e).__name__})"
    if name == "pymtex" and "pymtex" not in out:
        import importlib.metadata as md  # noqa: PLC0415

        try:
            out["pymtex"] = {"app": __version__, "mtex": md.version("mtex")}
        except md.PackageNotFoundError:
            out["pymtex"] = "mtex not installed"
    return out


def system_info() -> dict:
    from .sysinfo import gb, memory_status, system_summary  # noqa: PLC0415

    s = system_summary()
    try:
        m = memory_status()
        mem = {"app": gb(m["app"]), "python": gb(m["python"]), "matlab_engine": gb(m["matlab"]) if m["matlab_running"]
               else "not running", "pc_used": gb(m["pc_used"]), "pc_total": gb(m["pc_total"])}
    except Exception:  # noqa: BLE001
        mem = {}
    return {"os": s["os"], "machine": platform.machine(), "python": s["python"], "ram": s["ram"],
            "cpu_count": s["cpu_count"], "memory": mem}


def recipe_steps(project) -> list[dict]:
    if project is None:
        return []
    out = []
    from .sample import indexing_label  # noqa: PLC0415

    labels = {m.id: indexing_label(m) for m in project.files()}
    for s in project.recipe().steps:
        t = {k: v for k, v in s.metrics.items() if k.endswith("_s") or k in ("seconds", "peak_GB", "cached")}
        mid = s.params.get("map_id")
        out.append({"id": s.id, "op": s.op, "map": (f"{mid} [{labels[mid]}]" if labels.get(mid) else mid)
                    or s.params.get("file") and Path(str(s.params.get("file"))).name, "sample": s.sample_id, "backend": s.backend,
                    "status": "stale" if s.stale else "current", "time": s.timestamp, "timings": t})
    return out


def settings_info() -> dict:
    from .settings import load_settings  # noqa: PLC0415

    s = load_settings()
    fig = s.figure.model_dump(mode="json")
    paths = {k: (Path(v).name if v else v) for k, v in s.paths.model_dump(mode="json").items()}
    return {"backend": s.backend, "figure": fig, "paths (file names only)": paths}


def _md_table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "(none)\n"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "".join("| " + " | ".join(str(r.get(c, "")).replace("|", "/") for c in cols) + " |\n" for r in rows)


def build(description: str, category: str, context: dict | None = None, project=None,
          log_lines: list[str] | None = None) -> dict:
    """The report as a dict (the JSON twin); render_md() makes the Markdown from it."""
    ctx = {k: (shorten_paths(str(v)) if v is not None else None) for k, v in (context or {}).items()}
    proj = None
    if project is not None:
        info = project.info()
        proj = {"name": info.name, "folder": project.root.name, "backend": info.backend,
                "files": len(project.files()), "samples": [s.id for s in project.sample_infos()]}
    return {
        "description": shorten_paths((description or "").strip()) or "(no description)",
        "category": CATEGORIES.get(category, category),
        "date": datetime.now().isoformat(timespec="seconds"),
        "app": {"version": __version__, "git_commit": git_commit(), "started": bc.STARTED.isoformat(timespec="seconds")},
        "backend": backend_info(project),
        "system": system_info(),
        "state": {"project": proj, **ctx},
        "ui_actions": bc.last(N_ACTIONS),
        "recipe": recipe_steps(project),
        "errors": bc.errors(),
        "log": (log_tail(N_LOG) if log_lines is None else [shorten_paths(x) for x in log_lines][-N_LOG:]),
        "settings": settings_info(),
    }


def render_md(d: dict) -> str:
    L = [f"# Ti64 report for Claude: {d['category']} ({d['date']})", "", "## Description", "", d["description"], ""]
    L += ["## App", "", f"- version: {d['app']['version']}", f"- git commit: {d['app']['git_commit']}",
          f"- running since: {d['app']['started']}", ""]
    L += ["## Backend", "", "```json", json.dumps(d["backend"], indent=2, default=str), "```", ""]
    sy = d["system"]
    L += ["## System", "", f"- OS: {sy['os']} ({sy['machine']}), Python {sy['python']}, {sy['cpu_count']} CPUs",
          f"- RAM: {sy['ram']}; memory use: " + ", ".join(f"{k} {v}" for k, v in sy.get("memory", {}).items()), ""]
    L += ["## Current state", ""] + [f"- {k}: {v}" for k, v in d["state"].items()
                                       if k != "project" and v not in (None, "", "None")]
    p = d["state"].get("project")
    L += [f"- project: {p['name']} (folder {p['folder']}, backend {p['backend']}, {p['files']} files, samples "
          f"{', '.join(p['samples']) or 'none'})" if p else "- project: none open", ""]
    L += [f"## Last UI actions ({len(d['ui_actions'])}, oldest first)", "",
          _md_table(d["ui_actions"], ["time", "kind", "text"])]
    rec = [{**r, "timings": ", ".join(f"{k} {v:g}" for k, v in r["timings"].items())} for r in d["recipe"]]
    L += [f"## Recipe ({len(rec)} steps)", "", _md_table(rec, ["id", "op", "map", "sample", "backend", "status",
                                                                "time", "timings"])]
    L += [f"## Errors since start ({len(d['errors'])})", ""]
    for e in d["errors"] or []:
        L += [f"### {e['time']} {e['type']}: {e['what'] or e['message']}", "", "```", e["traceback"] or e["message"],
              "```", ""]
    if not d["errors"]:
        L += ["(none)", ""]
    L += [f"## Log (last {len(d['log'])} lines)", "", "```", *d["log"], "```", ""]
    L += ["## Settings", "", "```json", json.dumps(d["settings"], indent=2, default=str), "```", ""]
    if d.get("screenshot"):
        L += ["## Screenshot", "", f"![screenshot]({d['screenshot']})", ""]
    return "\n".join(L)


def save_report(description: str, category: str, context: dict | None = None, project=None,
                folder: Path | None = None, screenshot_png: bytes | None = None,
                log_lines: list[str] | None = None) -> tuple[Path, Path]:
    """Write reports/YYYY-MM-DD_HHMM_<category>.md + .json (+ .png); returns (md, json)."""
    folder = Path(folder or REPORTS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y-%m-%d_%H%M}_{category}"
    i = 2
    while (folder / f"{stem}.md").exists():
        stem = f"{datetime.now():%Y-%m-%d_%H%M}_{category}_{i}"
        i += 1
    d = build(description, category, context, project, log_lines)
    if screenshot_png:
        (folder / f"{stem}.png").write_bytes(screenshot_png)
        d["screenshot"] = f"{stem}.png"
    md, js = folder / f"{stem}.md", folder / f"{stem}.json"
    for f, text in ((md, render_md(d)), (js, json.dumps(d, indent=2, default=str))):
        tmp = f.with_name(f".{f.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(f)
    bc.add("report", f"saved {md.name}")
    return md, js
