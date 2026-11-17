"""Project folder (default projects/<name>/, git-ignored; New Project in the GUI can put it anywhere):

  project.json      name, backend, created, app version, format version
  recipe.json       steps (ti64app.recipe); the last 20 versions are kept in .history/recipe/
  samples.json      samples (SampleInfo: specimen data, entered once)          [format 2]
  files.json        linked files (MapInfo: absolute path, size, mtime, sample, map sheet)  [format 2]
  annotations.json  annotations / ROIs
  gui_state.json    GUI working state: open tab, selected file, viewer zoom / position, overlays, unapplied drafts
  export_log.json   exports (settings, file, date) for "export again"; exports are not recipe steps (fix 3.4)
  exports/<id>/     exported files kept in the project
  running.json      the step being run (left behind if the app closed during a step -> "interrupted")
  outputs/<sample>_<step>/  step outputs (npz / json / png ...); outputs/<step>/ for steps without a sample
  maps/             maps cropped inside the project
  figures/, cache/  (cache = rebuilt from the recipe, "Clear cache" in Settings)
  .history/         undo / redo snapshots (undo/) and recipe versions (recipe/); survives closing the app

Every state file is written atomically (temp file + rename). Format 1 (samples/<map_id>.json with one full
sample sheet per map) is migrated on open (docs/decisions.md D18).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from . import __version__
from .recipe import Recipe
from .sample import (MapInfo, SampleInfo, SampleSheet, effective_sheet, file_kind, map_id_from_file, split_sheet,
                     suggest_input_state)
from .settings import BackendName, REPO_ROOT, preferences_path

PROJECTS_DIR = REPO_ROOT / "projects"
FORMAT = 2
STATE_FILES = ("project.json", "recipe.json", "samples.json", "files.json", "annotations.json")


def projects_dir() -> Path:
    """Where projects live by default: TI64_PROJECTS_DIR if set, else <repo>/projects (git-ignored)."""
    return Path(os.environ.get("TI64_PROJECTS_DIR") or PROJECTS_DIR)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same folder + rename, so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(20):                      # Windows: the target may be open in a reader for a moment
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    tmp.replace(path)


def write_json(path: Path, obj) -> None:
    atomic_write_text(Path(path), json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def read_json(path: Path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


class ProjectInfo(BaseModel):
    name: str
    backend: BackendName = "pymtex"
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    app_version: str = __version__
    format_version: int = FORMAT


class Project:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths --------------------------------------------------------------------------------
    @property
    def info_path(self) -> Path:
        return self.root / "project.json"

    @property
    def recipe_path(self) -> Path:
        return self.root / "recipe.json"

    @property
    def samples_path(self) -> Path:
        return self.root / "samples.json"

    @property
    def files_path(self) -> Path:
        return self.root / "files.json"

    @property
    def gui_state_path(self) -> Path:
        return self.root / "gui_state.json"

    @property
    def samples_dir(self) -> Path:            # format 1 only (migrated)
        return self.root / "samples"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def history_dir(self) -> Path:
        return self.root / ".history"

    @property
    def name(self) -> str:
        return self.info().name

    def step_dir(self, step_id: str, sample_id: str | None = None) -> Path:
        """Output folder of a step: outputs/<sample>_<step> (outputs/<step> without a sample)."""
        return self.outputs_dir / (f"{sample_id}_{step_id}" if sample_id else step_id)

    def find_step_dir(self, step_id: str) -> Path | None:
        d = self.outputs_dir / step_id
        if d.is_dir():
            return d
        return next((q for q in self.outputs_dir.glob(f"*_{step_id}") if q.is_dir()), None)

    # -- create / open ------------------------------------------------------------------------
    @classmethod
    def create(cls, name: str, base: Path | None = None, backend: str = "pymtex") -> "Project":
        root = Path(base or projects_dir()) / name
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"{root} exists and is not empty")
        p = cls(root)
        for d in (root, p.outputs_dir, p.figures_dir, p.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        p.save_info(ProjectInfo(name=name, backend=backend))
        Recipe().save(p.recipe_path)
        write_json(p.samples_path, {"samples": []})
        write_json(p.files_path, {"files": []})
        return p

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = Path(root)
        if not (root / "project.json").is_file():
            raise FileNotFoundError(root / "project.json")
        p = cls(root)
        p.migrate()
        return p

    @classmethod
    def open_or_create(cls, name: str, base: Path | None = None) -> "Project":
        root = Path(base or projects_dir()) / name
        return cls.open(root) if (root / "project.json").is_file() else cls.create(name, base)

    # -- migration (format 1 -> 2) ---------------------------------------------------------------
    def migrate(self) -> list[str]:
        """Migrate an old project in place (also its undo snapshots); returns what was done."""
        done = migrate_dir(self.root)
        old_hist = self.cache_dir / "history"           # undo stack used to live in cache/ (cleared by Clear cache)
        new_hist = self.history_dir / "undo"
        if old_hist.is_dir() and not new_hist.exists():
            new_hist.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_hist), str(new_hist))
            done.append("undo history moved from cache/history to .history/undo")
        if new_hist.is_dir():
            for snap in new_hist.iterdir():
                if snap.is_dir() and not (snap / "files.json").is_file():
                    migrate_dir(snap, backup=False)
        from .exportlog import migrate_recipe  # noqa: PLC0415

        n = migrate_recipe(self.root)                   # exports are not recipe steps (fix 3.4)
        if n:
            done.append(f"{n} export step(s) moved from the recipe to the export log")
        from .views import migrate_recipe as migrate_views  # noqa: PLC0415

        n = migrate_views(self.root)                    # figures drawn from results are view state (fix 6.7.1)
        if n:
            done.append(f"{n} grain-size figure step(s) moved from the recipe to the view settings (views.json)")
        return done

    # -- state --------------------------------------------------------------------------------
    def info(self) -> ProjectInfo:
        return ProjectInfo.model_validate_json(self.info_path.read_text(encoding="utf-8"))

    def save_info(self, info: ProjectInfo) -> None:
        atomic_write_text(self.info_path, info.model_dump_json(indent=1))

    def recipe(self) -> Recipe:
        return Recipe.load(self.recipe_path)

    def save_recipe(self, recipe: Recipe) -> None:
        recipe.save(self.recipe_path)

    # -- samples (SampleInfo) ------------------------------------------------------------------
    def sample_infos(self) -> list[SampleInfo]:
        return [SampleInfo.model_validate(s) for s in (read_json(self.samples_path, {}) or {}).get("samples", [])]

    def sample_info(self, sample_id: str) -> SampleInfo | None:
        return next((s for s in self.sample_infos() if s.id == sample_id), None)

    def save_sample_info(self, s: SampleInfo, old_id: str | None = None) -> None:
        """Add or replace a sample (renaming moves its files along)."""
        if old_id and old_id != s.id and self.sample_info(s.id) is not None:
            raise FileExistsError(f"sample {s.id!r} exists already")
        before = {m.id: self._sheet_or_none(m.id) for m in self.files() if m.sample_id in (s.id, old_id)}
        items = [x for x in self.sample_infos() if x.id not in (s.id, old_id)]
        write_json(self.samples_path, {"samples": [x.model_dump(mode="json") for x in items + [s]]})
        if old_id and old_id != s.id:
            self.save_files([m.model_copy(update={"sample_id": s.id}) if m.sample_id == old_id else m
                             for m in self.files()])
        for mid, old in before.items():
            self._stale_if_changed(mid, old)

    def delete_sample(self, sample_id: str) -> None:
        """Remove a sample; its files become Unassigned (never deleted)."""
        write_json(self.samples_path, {"samples": [x.model_dump(mode="json") for x in self.sample_infos()
                                                   if x.id != sample_id]})
        self.save_files([m.model_copy(update={"sample_id": None}) if m.sample_id == sample_id else m
                         for m in self.files()])

    # -- linked files (MapInfo) ------------------------------------------------------------------
    def files(self) -> list[MapInfo]:
        return [MapInfo.model_validate(m) for m in (read_json(self.files_path, {}) or {}).get("files", [])]

    def file(self, map_id: str) -> MapInfo | None:
        return next((m for m in self.files() if m.id == map_id), None)

    def save_files(self, files: list[MapInfo]) -> None:
        write_json(self.files_path, {"files": [m.model_dump(mode="json") for m in files]})

    def add_files(self, paths: list[str | Path], sample_id: str | None = None) -> list[MapInfo]:
        """Link files by absolute path (never copied). JEOL .txt sidecars are skipped (shown with their image);
        already linked files are returned unchanged."""
        files = self.files()
        by_path = {os.path.normcase(m.file): m for m in files}
        ids = {m.id for m in files}
        out = []
        for f in paths:
            f = Path(f).absolute()
            kind = file_kind(f)
            if kind is None:
                continue
            if os.path.normcase(str(f)) in by_path:
                out.append(by_path[os.path.normcase(str(f))])
                continue
            base = map_id_from_file(f) if kind == "ang" else _safe_id(f.stem)
            mid, i = base, 2
            while mid in ids:
                mid, i = f"{base}_{i}", i + 1
            st = f.stat() if f.is_file() else None
            m = MapInfo(id=mid, file=str(f), kind=kind, sample_id=sample_id,
                        size=st.st_size if st else None, mtime=st.st_mtime if st else None)
            files.append(m)
            ids.add(mid)
            by_path[os.path.normcase(str(f))] = m
            out.append(m)
        self.save_files(files)
        return out

    def remove_file(self, map_id: str) -> None:
        """Remove a file from the project only (the file on disk is never touched)."""
        self.save_files([m for m in self.files() if m.id != map_id])

    def assign(self, map_ids: list[str], sample_id: str | None) -> None:
        """Move files to a sample (None = Unassigned). A file belongs to one sample."""
        if sample_id is not None and self.sample_info(sample_id) is None:
            raise KeyError(f"no sample {sample_id!r}")
        ids = set(map_ids)
        before = {m.id: self._sheet_or_none(m.id) for m in self.files() if m.id in ids}
        self.save_files([m.model_copy(update={"sample_id": sample_id}) if m.id in ids else m for m in self.files()])
        for mid, old in before.items():
            self._stale_if_changed(mid, old)

    def update_file(self, map_id: str, **changes) -> MapInfo:
        """Change map-sheet fields of one file (autosave from the Files tab). Changing anything that the results
        depend on (input state, build direction, position, 4PB, sample) marks that map's steps stale."""
        files = self.files()
        i = next((k for k, m in enumerate(files) if m.id == map_id), None)
        if i is None:
            raise KeyError(f"no file {map_id!r} in the project")
        old = self._sheet_or_none(map_id)
        data = files[i].model_dump()
        data.update(changes)
        files[i] = MapInfo.model_validate(data)
        self.save_files(files)
        self._stale_if_changed(map_id, old)
        return files[i]

    def touch_file(self, path: str | Path) -> None:
        """Record size + mtime of a linked file when a step uses it (for the 'changed since used' warning)."""
        files = self.files()
        key = os.path.normcase(str(Path(path).absolute()))
        for m in files:
            if os.path.normcase(m.file) == key and Path(m.file).is_file():
                st = Path(m.file).stat()
                if (m.size, m.mtime) != (st.st_size, st.st_mtime):
                    m.size, m.mtime = st.st_size, st.st_mtime
                    self.save_files(files)
                return

    @staticmethod
    def file_status(m: MapInfo) -> str:
        """'ok' | 'missing' | 'changed' (size or modification time differ from when it was linked / last used)."""
        f = Path(m.file)
        if not f.is_file():
            return "missing"
        st = f.stat()
        if m.size is not None and (st.st_size != m.size or abs(st.st_mtime - (m.mtime or 0)) > 1e-3):
            return "changed"
        return "ok"

    def find_missing(self, folder: str | Path, max_files: int = 200_000) -> dict[str, list[str]]:
        """Search `folder` recursively for the linked files that are missing: by file name (case-insensitive); when
        several files have that name, the ones with the recorded size come first. Returns {map_id: [candidates]}
        for every missing file (empty list = not found)."""
        missing = [m for m in self.files() if not Path(m.file).is_file()]
        want = {Path(m.file).name.lower() for m in missing}
        hits: dict[str, list[str]] = {}
        n = 0
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
            for fn in filenames:
                n += 1
                if fn.lower() in want:
                    hits.setdefault(fn.lower(), []).append(str(Path(dirpath) / fn))
            if n > max_files:
                break
        out: dict[str, list[str]] = {}
        for m in missing:
            c = hits.get(Path(m.file).name.lower(), [])
            if m.size is not None:
                c = sorted(c, key=lambda f: Path(f).stat().st_size != m.size)
            out[m.id] = c
        return out

    def relocate(self, map_id: str, new_path: str | Path) -> MapInfo:
        f = Path(new_path).absolute()
        st = f.stat()
        return self.update_file(map_id, file=str(f), size=st.st_size, mtime=st.st_mtime)

    # -- effective map sheets (what the EBSD ops use) -------------------------------------------
    def sample_of(self, map_id: str | None) -> str | None:
        m = self.file(map_id) if map_id else None
        return m.sample_id if m else None

    def sample(self, map_id: str) -> SampleSheet:
        """Effective sheet of one map = its MapInfo + inherited sample values."""
        m = self.file(map_id)
        if m is None:
            raise KeyError(f"no sample sheet for map {map_id!r}; add the file on the Files tab and enter its "
                           "sheet (or MCP set_sample_sheet)")
        return effective_sheet(m, self.sample_info(m.sample_id) if m.sample_id else None)

    def samples(self) -> list[SampleSheet]:
        """Effective sheets of every complete .ang map (incomplete ones are skipped)."""
        out = []
        for m in self.files():
            if m.kind == "ang":
                try:
                    out.append(self.sample(m.id))
                except (KeyError, ValueError):
                    pass
        return out

    def set_sheet(self, sheet: SampleSheet) -> None:
        """Store a full legacy sheet (op sample.set, MCP set_sample_sheet): the sample is created if needed,
        values that differ from it become per-file overrides; the file is linked if it is not yet. A sheet without a
        sample (loaded before the user assigned one) links the file Unassigned; an input state that only repeats the
        suggestion from the file name is not stored as if the user had entered it."""
        files = self.files()
        m = next((x for x in files if x.id == sheet.map_id), None)
        if sheet.specimen_id:
            s, fields = split_sheet(sheet, self.sample_info(sheet.specimen_id))
            if self.sample_info(s.id) is None:
                write_json(self.samples_path,
                           {"samples": [x.model_dump(mode="json") for x in self.sample_infos() + [s]]})
            sid = s.id
        else:
            sid = None
            fields = {"input_state": sheet.input_state, "build_direction": sheet.build_direction,
                      "position": sheet.position, "notes": sheet.notes, "phase_map": sheet.phase_map,
                      "axes": sheet.axes}
            for k in ("material", "process", "bending"):
                if m is not None and getattr(m, k) is not None:
                    fields[k] = getattr(sheet, k)
        if (m is not None and m.input_state is None and fields.get("input_state") == suggest_input_state(sheet.map_file)):
            fields["input_state"] = None
        if sheet.axes is not None and sheet.axes.mode == "approximate":
            # the effective BD of 'approximate' map axes is derived from the BD axis: it must not overwrite the exact
            # build direction the map sheet keeps for 'exact' mode (fix 6.7.1)
            fields.pop("build_direction", None)
        f = Path(sheet.map_file)
        st = f.stat() if f.is_file() else None
        base = {"id": sheet.map_id, "file": str(f), "kind": file_kind(f) or "other", "sample_id": sid,
                "size": st.st_size if st else None, "mtime": st.st_mtime if st else None}
        new = MapInfo.model_validate({**(m.model_dump() if m else {}), **base, **fields,
                                      **{k: fields.get(k) for k in ("material", "process", "bending")}})
        files = [x for x in files if x.id != sheet.map_id] + [new]
        self.save_files(files)

    def _sheet_or_none(self, map_id: str):
        try:
            return self.sample(map_id).model_dump(mode="json", exclude={"notes", "specimen_id", "material", "process"})
        except (KeyError, ValueError):
            return None

    def _stale_if_changed(self, map_id: str, old) -> int:
        from .sample import stale_on_sheet_change  # noqa: PLC0415

        new = self._sheet_or_none(map_id)
        if old is None or new == old:
            return 0
        pred = stale_on_sheet_change(map_id, old, new or {})
        r = self.recipe()
        n = r.mark_stale(lambda s: s.op != "sample.set" and pred(s))
        if n:
            self.save_recipe(r)
        return n

    # -- backend ---------------------------------------------------------------------------------
    def set_backend(self, backend: str) -> int:
        """Switch the project's backend; marks backend-dependent results stale. Returns how many."""
        info = self.info()
        if info.backend == backend:
            return 0
        info.backend = backend
        self.save_info(info)
        r = self.recipe()
        n = r.mark_stale(lambda s: s.backend is not None and s.backend != backend)
        self.save_recipe(r)
        return n

    def summary(self) -> dict:
        info = self.info()
        r = self.recipe()
        return {"name": info.name, "root": str(self.root), "backend": info.backend,
                "maps": [s.map_id for s in self.samples()],
                "samples": {s.id: [m.id for m in self.files() if m.sample_id == s.id] for s in self.sample_infos()},
                "steps": [{"id": s.id, "op": s.op, "sample": s.sample_id, "backend": s.backend, "stale": s.stale}
                          for s in r.steps]}

    # -- GUI working state, interrupted steps -----------------------------------------------------
    def gui_state(self) -> dict:
        try:
            return read_json(self.gui_state_path, {}) or {}
        except (OSError, ValueError):
            return {}

    def save_gui_state(self, state: dict) -> None:
        write_json(self.gui_state_path, state)

    def interrupted(self) -> dict | None:
        """The step that was running when the app closed (running.json left behind by a dead process)."""
        d = read_json(self.root / "running.json")
        if not d:
            return None
        try:
            import psutil  # noqa: PLC0415

            if d.get("pid") != os.getpid() and psutil.pid_exists(d.get("pid", -1)):
                p = psutil.Process(d["pid"])
                if abs(p.create_time() - d.get("pid_started", 0)) < 2:
                    return None                       # still running in another process (MCP server, CLI)
        except Exception:  # noqa: BLE001
            pass
        if d.get("pid") == os.getpid():
            return None
        return d

    def clear_interrupted(self) -> None:
        (self.root / "running.json").unlink(missing_ok=True)

    # -- disk use, cache, copy ---------------------------------------------------------------------
    def disk_usage(self) -> dict[str, int]:
        def size(p: Path) -> int:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0
        d = {"cache": size(self.cache_dir), "outputs": size(self.outputs_dir), "history": size(self.history_dir),
             "maps": size(self.root / "maps") + size(self.figures_dir)}
        d["total"] = size(self.root)
        return d

    def clear_cache(self) -> int:
        """Delete cache/ (layers, viewer tiles, compare scratch copies); rebuilt from the recipe when needed.
        Returns the bytes freed."""
        n = self.disk_usage()["cache"]
        if self.cache_dir.is_dir():
            for q in self.cache_dir.iterdir():
                (shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink(missing_ok=True))
        return n

    def save_copy(self, dest_root: Path, name: str | None = None) -> "Project":
        """Duplicate the project (links, recipe, samples, annotations, outputs, GUI state; NOT the cache or the undo
        history) to try alternative settings. Outputs are hard-linked when possible."""
        dest_root = Path(dest_root)
        if dest_root.exists() and any(dest_root.iterdir()):
            raise FileExistsError(f"{dest_root} exists and is not empty")
        dest_root.mkdir(parents=True, exist_ok=True)
        for f in (*STATE_FILES, "gui_state.json", "export_log.json"):
            if (self.root / f).is_file():
                shutil.copy2(self.root / f, dest_root / f)
        for sub in ("outputs", "exports", "maps", "figures"):
            src = self.root / sub
            for f in (src.rglob("*") if src.is_dir() else []):
                if f.is_file():
                    dst = dest_root / sub / f.relative_to(src)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(f, dst)
                    except OSError:
                        shutil.copy2(f, dst)
        (dest_root / "cache").mkdir(exist_ok=True)
        p = Project(dest_root)
        info = p.info()
        info.name = name or dest_root.name
        p.save_info(info)
        return p


def _safe_id(stem: str) -> str:
    import re  # noqa: PLC0415

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_")[:64] or "file"


def migrate_dir(root: Path, backup: bool = True) -> list[str]:
    """Format 1 -> 2: samples/<map_id>.json (one full SampleSheet per map) -> samples.json (SampleInfo per specimen)
    + files.json (MapInfo per file with per-file overrides where maps of one specimen differed). The old folder is
    kept as .history/format1_samples/ (backup)."""
    root = Path(root)
    old = root / "samples"
    if (root / "files.json").is_file() or not old.is_dir():
        if not (root / "files.json").is_file() and (root / "project.json").is_file() and not old.is_dir():
            write_json(root / "files.json", {"files": []})
            if not (root / "samples.json").is_file():
                write_json(root / "samples.json", {"samples": []})
        return []
    sheets = []
    for f in sorted(old.glob("*.json")):
        try:
            sheets.append(SampleSheet.model_validate_json(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    samples: dict[str, SampleInfo] = {}
    files = []
    for sh in sheets:
        s, fields = split_sheet(sh, samples.get(sh.specimen_id))
        samples.setdefault(s.id, s)
        f = Path(sh.map_file)
        st = f.stat() if f.is_file() else None
        files.append(MapInfo(id=sh.map_id, file=sh.map_file, kind="ang", sample_id=s.id,
                             size=st.st_size if st else None, mtime=st.st_mtime if st else None, **fields))
    write_json(root / "samples.json", {"samples": [s.model_dump(mode="json") for s in samples.values()]})
    write_json(root / "files.json", {"files": [m.model_dump(mode="json") for m in files]})
    if backup:
        dst = root / ".history" / "format1_samples"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(old), str(dst))
    else:
        shutil.rmtree(old)
    if (root / "project.json").is_file():
        d = json.loads((root / "project.json").read_text(encoding="utf-8"))
        d["format_version"] = FORMAT
        write_json(root / "project.json", d)
    return [f"migrated {len(sheets)} sample sheets to {len(samples)} samples + {len(files)} files"]


def list_projects(base: Path | None = None) -> list[str]:
    base = Path(base or projects_dir())
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "project.json").is_file())


# ---- recent projects (Open Project list) --------------------------------------------------------------
def _recent_path() -> Path:
    return preferences_path().parent / "recent_projects.json"


def recent_projects(limit: int = 10) -> list[str]:
    """Roots of recently opened projects that still exist, newest first."""
    try:
        roots = read_json(_recent_path(), []) or []
    except (OSError, ValueError):
        roots = []
    return [r for r in roots if (Path(r) / "project.json").is_file()][:limit]


def add_recent(root: Path) -> None:
    r = str(Path(root).absolute())
    roots = [x for x in (read_json(_recent_path(), []) or []) if os.path.normcase(x) != os.path.normcase(r)]
    write_json(_recent_path(), [r] + roots[:19])


def resolve_project(ref: str | Path | None) -> Path | None:
    """A project given by root path or by name under projects_dir()."""
    if not ref:
        return None
    p = Path(ref)
    if p.is_absolute() or len(p.parts) > 1:
        return p
    return projects_dir() / str(ref)
