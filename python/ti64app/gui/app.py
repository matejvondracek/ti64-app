"""NiceGUI front end (localhost). Pages: Project (/), Sample sheet (/sample), Maps (/maps),
Prior-beta reconstruction (/recon), Settings (/settings).

All actions go through ti64app.service (recorded in recipe.json), the same as the MCP tools.
Run:  python -m ti64app.gui.app      -> http://127.0.0.1:8064
"""
from __future__ import annotations

import traceback
from pathlib import Path

from nicegui import run as ng_run
from nicegui import ui

from .. import service
from ..backends import backend_status, get_backend
from ..project import Project, list_projects, projects_dir
from ..sample import (BuildDirection, FourPointBending, MapPosition, SampleSheet, map_id_from_file,
                      suggest_input_state)
from ..settings import IpfKeySetting, load_settings, save_preferences

AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
STATE: dict = {"project": None}


def current_project() -> Project | None:
    name = STATE["project"]
    if not name:
        return None
    root = projects_dir() / name
    return Project.open(root) if (root / "project.json").is_file() else None


def _nav() -> None:
    with ui.header().classes("items-center gap-4"):
        ui.label("Ti64 EBSD/BSE").classes("text-lg font-bold")
        ui.link("Project", "/").classes("text-white")
        ui.link("Sample sheet", "/sample").classes("text-white")
        ui.link("Maps", "/maps").classes("text-white")
        ui.link("Reconstruction", "/recon").classes("text-white")
        ui.link("Grain size", "/grainsize").classes("text-white")
        ui.link("Settings", "/settings").classes("text-white")
        p = current_project()
        ui.label(f"project: {p.name} ({p.info().backend})" if p else "no project open").classes("ml-auto")


def _error(e: Exception) -> None:
    ui.notify(f"{type(e).__name__}: {e}", type="negative", multi_line=True, timeout=10000)
    traceback.print_exc()


# ---- Project page -----------------------------------------------------------------------------
def project_page() -> None:
    _nav()
    with ui.column().classes("p-4 gap-4 w-full max-w-5xl"):
        with ui.row().classes("items-end gap-2"):
            names = list_projects(projects_dir())
            sel = ui.select(names, label="Open project", value=STATE["project"]).classes("w-64")
            ui.button("Open", on_click=lambda: (STATE.update(project=sel.value), ui.navigate.to("/")))
            new = ui.input("New project name").classes("w-64").mark("new_project")

            def create():
                try:
                    Project.create(new.value.strip(), base=projects_dir(),
                                   backend=load_settings().backend)
                    STATE["project"] = new.value.strip()
                    ui.navigate.to("/")
                except Exception as e:  # noqa: BLE001
                    _error(e)
            ui.button("Create", on_click=create).mark("create_project")

        p = current_project()
        if p is None:
            ui.label("Open or create a project.")
            return
        info = p.info()
        ui.label(f"{info.name} — backend {info.backend} — {p.root}").classes("text-gray-600")

        ui.label("Maps (sample sheets)").classes("text-md font-bold")
        sheets = p.samples()
        if not sheets:
            ui.label("No sample sheet yet: enter one on the Sample sheet page.")
        for s in sheets:
            with ui.row().classes("items-center gap-4"):
                ui.label(f"{s.map_id}: {s.specimen_id}, {s.input_state}, BD {tuple(round(c, 3) for c in s.build_direction.unit_vector)}")

                async def load(map_id=s.map_id):
                    n = ui.notification(f"loading {map_id} with {p.info().backend} ...", spinner=True, timeout=None)
                    try:
                        step = await ng_run.io_bound(service.run, p, "ebsd.load", {"map_id": map_id})
                        ui.notify(f"{map_id}: {step.metrics.get('n_points')} points, "
                                  f"load {step.metrics.get('load_s')} s", type="positive")
                        ui.navigate.to("/")
                    except Exception as e:  # noqa: BLE001
                        _error(e)
                    finally:
                        n.dismiss()
                ui.button("Load map", on_click=load).props("dense")

        ui.label("Other files").classes("text-md font-bold")
        with ui.row().classes("items-end gap-2"):
            up2 = ui.input(".up2 file").classes("w-96")
            ui.button("Read .up2 header", on_click=lambda: _run_simple(p, "up2.header", {"file": up2.value}))
        with ui.row().classes("items-end gap-2"):
            img = ui.input("BSE image (.tif/.bmp)").classes("w-96")
            txt = ui.input("JEOL .txt (optional)").classes("w-64")
            ui.button("Load BSE", on_click=lambda: _run_simple(p, "bse.load", {"image": img.value, "txt": txt.value or None}))

        ui.label("Recipe").classes("text-md font-bold")
        rows = [{"id": s.id, "op": s.op, "backend": s.backend or "-", "stale": "stale" if s.stale else "",
                 "time": s.timestamp, "metrics": ", ".join(f"{k}={v}" for k, v in s.metrics.items())}
                for s in p.recipe().steps]
        ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in
                          ("id", "op", "backend", "stale", "time", "metrics")], rows=rows).classes("w-full")

        async def do_replay():
            n = ui.notification("replaying ...", spinner=True, timeout=None)
            try:
                rep = await ng_run.io_bound(service.replay, p)
                bad = [c for c in rep.checks if c.status not in ("identical", "within_tolerance")]
                ui.notify("replay reproduced all outputs" if rep.ok else f"replay: {len(bad)} differences, "
                          f"{len(rep.input_changes)} input changes", type="positive" if rep.ok else "warning")
            except Exception as e:  # noqa: BLE001
                _error(e)
            finally:
                n.dismiss()
        ui.button("Replay recipe", on_click=do_replay)


def _run_simple(p: Project, op: str, params: dict) -> None:
    try:
        step = service.run(p, op, params)
        ui.notify(f"{op} done ({step.id})", type="positive")
        ui.navigate.to("/")
    except Exception as e:  # noqa: BLE001
        _error(e)


# ---- Sample sheet page ------------------------------------------------------------------------
def sample_page() -> None:
    _nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-3xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        existing = {s.map_id: s for s in p.samples()}
        pick = ui.select(["(new)"] + list(existing), value="(new)", label="Edit sample sheet").classes("w-64")

        f = {}
        f["map_file"] = ui.input("Map file (.ang)").classes("w-full").mark("map_file")
        f["map_id"] = ui.input("Map ID").classes("w-64").mark("map_id")
        f["specimen_id"] = ui.input("Specimen ID (required)").classes("w-64").mark("specimen_id")
        f["input_state"] = ui.toggle({"raw": "raw", "oim_cleaned": "OIM-cleaned"}, value=None).mark("input_state")
        ui.label("Input state is suggested from the file name; confirm it.").classes("text-xs text-gray-500")

        def suggest():
            if f["map_file"].value:
                if not f["map_id"].value:
                    f["map_id"].value = map_id_from_file(f["map_file"].value)
                if f["input_state"].value is None:
                    f["input_state"].value = suggest_input_state(f["map_file"].value)
        f["map_file"].on("blur", suggest)

        ui.label("Build direction (required, map frame: x right, y down)").classes("font-bold")
        f["bd_mode"] = ui.toggle({"angle": "angle", "vector": "vector"}, value="angle")
        f["bd_angle"] = ui.number("angle from +x toward +y [deg]", value=None).mark("bd_angle")
        with ui.row():
            f["bd_x"], f["bd_y"], f["bd_z"] = (ui.number(c, value=None).classes("w-24") for c in ("x", "y", "z"))

        ui.label("4-point bending (mm, N) — all four geometry values or none").classes("font-bold")
        with ui.row():
            for k, lab in (("outer_span_mm", "outer span"), ("inner_span_mm", "inner span"),
                           ("width_mm", "width"), ("thickness_mm", "thickness"), ("load_N", "load [N]")):
                f[k] = ui.number(lab, value=None).classes("w-28")

        ui.label("Map position on the specimen (sample frame, mm)").classes("font-bold")
        with ui.row():
            for k in ("origin_X_mm", "origin_Y_mm", "origin_Z_mm"):
                f[k] = ui.number(k, value=None).classes("w-28")
            f["map_x_axis"] = ui.select(AXES, value=None, label="map +x along").classes("w-28")
            f["map_y_axis"] = ui.select(AXES, value=None, label="map +y along").classes("w-28")
        f["notes"] = ui.textarea("Notes").classes("w-full")

        def fill(map_id):
            s = existing.get(map_id)
            if s is None:
                return
            f["map_file"].value, f["map_id"].value, f["specimen_id"].value = s.map_file, s.map_id, s.specimen_id
            f["input_state"].value = s.input_state
            bd = s.build_direction
            f["bd_mode"].value = "angle" if bd.angle_deg is not None else "vector"
            f["bd_angle"].value = bd.angle_deg
            if bd.vector:
                f["bd_x"].value, f["bd_y"].value, f["bd_z"].value = bd.vector
            for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm", "load_N"):
                f[k].value = getattr(s.bending, k) if s.bending else None
            for k in ("origin_X_mm", "origin_Y_mm", "origin_Z_mm", "map_x_axis", "map_y_axis"):
                f[k].value = getattr(s.position, k) if s.position else None
            f["notes"].value = s.notes
        pick.on_value_change(lambda e: fill(e.value))

        def save():
            try:
                sheet = sheet_from_form({k: w.value for k, w in f.items()})
                step = service.run(p, "sample.set", sheet)
                ui.notify(f"sample sheet {sheet.map_id} saved ({step.id})", type="positive")
            except Exception as e:  # noqa: BLE001
                _error(e)
        ui.button("Save sample sheet", on_click=save).mark("save_sample")


def sheet_from_form(v: dict) -> SampleSheet:
    """Build a SampleSheet from form values (shared by the GUI and its tests)."""
    if v.get("bd_mode") == "vector":
        bd = BuildDirection(vector=(v.get("bd_x") or 0.0, v.get("bd_y") or 0.0, v.get("bd_z") or 0.0))
    else:
        if v.get("bd_angle") is None:
            raise ValueError("build direction is required")
        bd = BuildDirection(angle_deg=float(v["bd_angle"]))
    geo = [v.get(k) for k in ("outer_span_mm", "inner_span_mm", "width_mm", "thickness_mm")]
    bend = None
    if any(g is not None for g in geo):
        if any(g is None for g in geo):
            raise ValueError("4PB: enter outer span, inner span, width and thickness (or none)")
        bend = FourPointBending(outer_span_mm=geo[0], inner_span_mm=geo[1], width_mm=geo[2],
                                thickness_mm=geo[3], load_N=v.get("load_N"))
    pos_keys = ("origin_X_mm", "origin_Y_mm", "origin_Z_mm", "map_x_axis", "map_y_axis")
    pos = None
    if any(v.get(k) is not None for k in pos_keys):
        pos = MapPosition(**{k: v[k] for k in pos_keys if v.get(k) is not None})
    if not v.get("input_state"):
        raise ValueError("choose the input state (raw / OIM-cleaned)")
    return SampleSheet(map_id=v.get("map_id") or map_id_from_file(v["map_file"]),
                       specimen_id=(v.get("specimen_id") or "").strip(), map_file=v.get("map_file") or "",
                       input_state=v["input_state"], build_direction=bd, bending=bend, position=pos,
                       notes=v.get("notes") or "")


# ---- Settings page ----------------------------------------------------------------------------
def settings_page() -> None:
    _nav()
    s = load_settings()
    status = backend_status()
    with ui.column().classes("p-4 gap-3 w-full max-w-3xl"):
        ui.label("Analysis backend").classes("text-md font-bold")
        avail = {n: n for n, r in status.items() if not r}
        radio = ui.radio(avail, value=s.backend if s.backend in avail else next(iter(avail), None)).props("inline").mark("backend")
        for n, r in status.items():
            if r:
                ui.label(f"{n}: unavailable — {r}").classes("text-orange-700")
            else:
                running = n == "matlab" and getattr(get_backend(n), "running", False)
                ui.label(f"{n}: available" + (" (engine running)" if running else "")).classes("text-green-700")
        p = current_project()
        if p:
            ui.label(f"Project {p.name} uses {p.info().backend}. Changing it marks backend results stale.")

        ui.label("IPF colour key (shared by both renderers)").classes("text-md font-bold")
        keys = {}
        for phase, k in s.figure.ipf.items():
            with ui.row().classes("items-center"):
                ui.label(phase).classes("w-24")
                keys[phase] = (ui.select(["ipfHKLKey", "ipfTSLKey", "ipfHSVKey", "ipfSpotKey"], value=k.key).classes("w-36"),
                               ui.select(["X", "Y", "Z", "build", "custom"], value=k.direction).classes("w-28"))
        with ui.row():
            dpi = ui.number("DPI", value=s.figure.dpi).classes("w-24")
            width = ui.number("width [cm]", value=s.figure.width_cm).classes("w-24")
            lw = ui.number("boundary line width", value=s.figure.boundary_line_width).classes("w-36")

        def save():
            try:
                st = load_settings()
                st.backend = radio.value
                for phase, (k, d) in keys.items():
                    st.figure.ipf[phase] = IpfKeySetting(key=k.value, direction=d.value)
                st.figure.dpi, st.figure.width_cm = int(dpi.value), float(width.value)
                st.figure.boundary_line_width = float(lw.value)
                path = save_preferences(st)
                msg = f"saved to {path}"
                pr = current_project()
                if pr:
                    n = pr.set_backend(radio.value)
                    msg += f"; project backend {radio.value}" + (f", {n} steps marked stale" if n else "")
                ui.notify(msg, type="positive")
            except Exception as e:  # noqa: BLE001
                _error(e)
        ui.button("Save settings", on_click=save).mark("save_settings")


# ---- Maps page (cleanup, grains, per-pixel maps, figures) -------------------------------------
FIG_KINDS = ["ipf", "phase", "ci", "iq", "fit", "prias", "kam", "grod", "gos", "gnd", "boundaries", "burgers", "ipf_key"]


def maps_page() -> None:
    _nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-6xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        sheets = {s.map_id: s for s in p.samples()}
        if not sheets:
            ui.label("Enter a sample sheet and load a map first.")
            return
        mid = ui.select(list(sheets), value=next(iter(sheets)), label="Map").classes("w-64").mark("map")
        out = ui.column()

        async def do(op: str, params: dict, label: str):
            n = ui.notification(f"{label} ({p.info().backend}) ...", spinner=True, timeout=None)
            try:
                step = await ng_run.io_bound(service.run, p, op, {"map_id": mid.value, **params})
                ui.notify(f"{label} done ({step.id}, {step.metrics.get('seconds')} s)", type="positive")
                if "figure" in step.outputs or "comparison" in step.outputs:
                    k = "figure" if "figure" in step.outputs else "comparison"
                    out.clear()
                    with out:
                        ui.image(str(p.root / step.outputs[k].path)).classes("w-full max-w-4xl")
                return step
            except Exception as e:  # noqa: BLE001
                _error(e)
            finally:
                n.dismiss()

        with ui.expansion("1 Cleanup (preset from the input state)", value=True).classes("w-full"):
            sh = sheets[mid.value]
            ui.label(f"input state: {sh.input_state} — preset "
                     f"{'CI < 0.1 removed, ' if sh.input_state == 'raw' else 'no CI filter, '}"
                     "noise at 3°: indexed grains ≤ 2 px and voids ≤ 4 px refilled").classes("text-sm")
            with ui.row().classes("items-end"):
                ci = ui.number("CI threshold (empty = preset)", value=None).classes("w-48")
                spike = ui.checkbox("wild-spike removal")
                hq = ui.checkbox("halfQuadraticFilter")

            def cleanup_params():
                ov = {}
                if ci.value is not None:
                    ov["ci_threshold"] = float(ci.value)
                if spike.value:
                    ov["spike_removal"] = True
                if hq.value:
                    ov["hq_filter"] = True
                return {"overrides": ov}
            ui.button("Run cleanup", on_click=lambda: do("ebsd.cleanup", cleanup_params(), "cleanup")).mark("run_cleanup")
        with ui.expansion("2 Grains and boundaries").classes("w-full"):
            with ui.row().classes("items-end"):
                hagb = ui.number("HAGB [°]", value=15).classes("w-28")
                lagb = ui.number("LAGB [°]", value=2).classes("w-28")
                tol = ui.number("Burgers tolerance [°]", value=3).classes("w-40")
            ui.button("Compute grains", on_click=lambda: do(
                "grains.calc", {"grains": {"hagb_deg": hagb.value, "lagb_deg": lagb.value},
                                "boundaries": {"burgers_tol_deg": tol.value}}, "grains")).mark("run_grains")
        with ui.expansion("3 Orientation maps (KAM, GROD, GOS, GND)").classes("w-full"):
            with ui.row().classes("items-end"):
                kind = ui.select(["kam", "grod", "gos", "gnd"], value="kam", label="map").classes("w-28")
                order = ui.select([1, 2, 3], value=1, label="KAM order").classes("w-28")
                cut = ui.number("KAM cutoff [°]", value=5).classes("w-32")
            ui.button("Compute", on_click=lambda: do(
                "map.compute", {"kind": kind.value, "kam_order": order.value, "kam_threshold_deg": cut.value},
                kind.value.upper())).mark("run_map")
        with ui.expansion("4 Figure", value=True).classes("w-full"):
            with ui.row().classes("items-end"):
                fk = ui.select(FIG_KINDS, value="ipf", label="figure").classes("w-32").mark("fig_kind")
                ov = ui.select(["hagb", "lagb", "interface", "burgers"], multiple=True, value=[],
                               label="overlays").classes("w-64")
                dirn = ui.select(["(Settings)", "X", "Y", "Z", "build"], value="(Settings)",
                                 label="IPF direction").classes("w-36")
                ph = ui.select(["Ti (alpha)", "Ti (beta)"], multiple=True, value=[], label="phases").classes("w-56")

            def fig_params():
                d = {"kind": fk.value, "overlays": ov.value or []}
                if dirn.value != "(Settings)":
                    d["direction"] = dirn.value
                if ph.value:
                    d["phases"] = ph.value
                return d
            ui.button("Draw", on_click=lambda: do("figure", fig_params(), f"figure {fk.value}")).mark("run_figure")
        with ui.expansion("5 Compare with an OIM image").classes("w-full"):
            with ui.row().classes("items-end"):
                img = ui.input("OIM image (data/reference/...)").classes("w-96")
                ck = ui.select(["ipf", "ci"], value="ipf", label="kind").classes("w-24")
            ui.button("Compare", on_click=lambda: do("compare.oim", {"image": img.value, "kind": ck.value},
                                                     "OIM comparison"))
        out


# ---- Prior-beta reconstruction page ---------------------------------------------------------------
def _floats(text: str) -> list[float]:
    return [float(t) for t in str(text).replace(";", ",").split(",") if t.strip()]


def _recon_summary(p: Project, step) -> None:
    """Summary table of a recon.run step (numbers as in EBSD_part2's summary)."""
    import json  # noqa: PLC0415

    s = json.loads((p.root / step.outputs["summary"].path).read_text(encoding="utf-8"))
    dev = s.get("or_dev_deg")
    or_txt = (f"{s['or_used']} (refined OR deviates {dev:.2f}° from Burgers; {s['or_rule']})"
              if dev is not None else f"{s['or_used']} ({s['or_rule']})")
    rows = [("backend", s["backend"]), ("OR", or_txt),
            ("prior β grains ≥ ECD min (all fragments)", f"{s['n_grains_min_ecd']} ({s['n_grains_all']})"),
            ("reconstructed α area", f"{100 * s['rec_frac']:.1f} %"),
            ("mean fit / fit > bad", f"{s['mean_fit_deg']:.2f}° / {100 * s['bad_fit_frac']:.1f} %"),
            ("fit quantiles 10/25/50/75/90 %", ", ".join(f"{v:.2f}" for v in s["fit_quantiles"])),
            ("area-weighted ECD", f"{s['ecd_aw_um']:.1f} µm"),
            ("bridged steps / split noise", f"{s['bridged_pct']:.1f} % / {s['split_pct']:.1f} %")]
    gs = s["grain_shape"]
    if gs.get("aspect_along_across"):
        rows.append(("caliper along / across BD (area-w., edge grains excluded)",
                     f"{gs['along_bd_um']['area_weighted_mean']:.1f} / "
                     f"{gs['across_bd_um']['area_weighted_mean']:.1f} µm, aspect "
                     f"{gs['aspect_along_across']['area_weighted_mean']:.2f} (n = {gs['n_used']})"))
    for w in gs.get("warnings", []):
        rows.append(("warning", w))
    ui.table(columns=[{"name": "k", "label": "", "field": "k", "align": "left"},
                      {"name": "v", "label": "", "field": "v", "align": "left"}],
             rows=[{"k": k, "v": str(v)} for k, v in rows]).props("dense flat").classes("w-full").mark("recon_summary")
    cols = ["config"] + s["sweep_columns"]
    ui.label("merge sweep").classes("font-bold mt-2")
    ui.table(columns=[{"name": c, "label": c, "field": c} for c in cols],
             rows=[{"config": f"{m} {t:g}°", **{c: (f"{v:.1f}" if isinstance(v, float) else v)
                                                for c, v in zip(cols[1:], r)}}
                   for (m, t), r in zip(s["sweep_configs"], s["sweep"])]).props("dense flat")


RECON_FIG_KINDS = ["parent_ipf", "alpha_parent_gb", "fit", "variant", "packet", "mis_map", "parent_key",
                   "mis_hist", "fit_hist", "ecd_hist", "n_variants_hist"]


def recon_page() -> None:
    _nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-6xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        sheets = {s.map_id: s for s in p.samples()}
        if not sheets:
            ui.label("Enter a sample sheet and load a map first.")
            return
        mid = ui.select(list(sheets), value=next(iter(sheets)), label="Map").classes("w-64").mark("recon_map")
        res = ui.column().classes("w-full")
        out = ui.column().classes("w-full")

        async def do(op: str, params: dict, label: str):
            n = ui.notification(f"{label} ({p.info().backend}) ...", spinner=True, timeout=None)
            try:
                step = await ng_run.io_bound(service.run, p, op, {"map_id": mid.value, **params})
                ui.notify(f"{label} done ({step.id}, {step.metrics.get('seconds')} s)", type="positive")
                return step
            except Exception as e:  # noqa: BLE001
                _error(e)
            finally:
                n.dismiss()

        with ui.expansion("1 Reconstruct (EBSD_part2.m method; input = loaded map)", value=True).classes("w-full"):
            with ui.row().classes("items-end"):
                method = ui.select(["variantGraph", "graph"], value="variantGraph", label="method").classes("w-36")
                thr = ui.number("threshold [°]", value=5.0).classes("w-28").mark("vg_thr")
                tol = ui.number("tolerance [°]", value=1.5).classes("w-28")
                seg = ui.number("α grain angle [°]", value=1.5).classes("w-32")
            with ui.row().classes("items-end"):
                or_mode = ui.select({"limit": "refined OR if ≤ limit, else Burgers", "burgers": "ideal Burgers",
                                     "refined": "refined OR always"}, value="limit", label="OR").classes("w-72")
                or_max = ui.number("limit [°]", value=1.0).classes("w-24")
                mmode = ui.select(["single", "progressive"], value="single", label="merge").classes("w-32")
                mthr = ui.number("merge threshold [°]", value=3.0).classes("w-36")
                incl = ui.number("inclusions < px", value=5).classes("w-32")

            def rparams() -> dict:
                d = {"method": method.value, "segAngle": seg.value, "orMode": or_mode.value,
                     "orMaxDev": or_max.value, "mergeMode": mmode.value, "mergeThreshold": mthr.value,
                     "inclusionMaxSize": int(incl.value)}
                if method.value == "variantGraph":
                    d.update(vgThreshold=thr.value, vgTolerance=tol.value)
                else:
                    d.update(graphThreshold=thr.value, graphTolerance=tol.value)
                return d

            async def run_recon():
                step = await do("recon.run", {"params": rparams()}, "reconstruction")
                if step:
                    res.clear()
                    with res:
                        _recon_summary(p, step)
            ui.button("Reconstruct", on_click=run_recon).mark("run_recon")
        with ui.expansion("2 Figures", value=True).classes("w-full"):
            with ui.row().classes("items-end"):
                fk = ui.select(RECON_FIG_KINDS, value="parent_ipf", label="figure").classes("w-48").mark("recon_fig")
                dirn = ui.select(["(Settings)", "X", "Y", "Z", "build"], value="(Settings)",
                                 label="IPF direction").classes("w-36")

            async def draw():
                d = {"kind": fk.value}
                if dirn.value != "(Settings)":
                    d["direction"] = dirn.value
                step = await do("recon.figure", d, f"figure {fk.value}")
                if step:
                    out.clear()
                    with out:
                        ui.image(str(p.root / step.outputs["figure"].path)).classes("w-full max-w-4xl")
            ui.button("Draw", on_click=draw).mark("run_recon_fig")
        with ui.expansion("3 Parameter sweep").classes("w-full"):
            ui.label("Every combination is reconstructed with the settings above; numbers + thumbnails.").classes("text-sm")
            with ui.row().classes("items-end"):
                sw_thr = ui.input("thresholds [°]", value="5, 8").classes("w-40").mark("sweep_thr")
                sw_merge = ui.input("merge thresholds [°]", value="0.5, 3, 6").classes("w-48").mark("sweep_merge")

            async def sweep():
                name = "vgThreshold" if method.value == "variantGraph" else "graphThreshold"
                step = await do("recon.sweep", {"base": {**rparams(), "sweep": []},
                                                "grid": {name: _floats(sw_thr.value),
                                                         "mergeThreshold": _floats(sw_merge.value)}}, "sweep")
                if not step:
                    return
                import json  # noqa: PLC0415

                t = json.loads((p.root / step.outputs["table"].path).read_text(encoding="utf-8"))
                out.clear()
                with out:
                    cols = ["run", "params", "grains", "all", "recFrac %", "fit p50", "fit p90", "ECDaw",
                            "bridged %", "split %"]
                    ui.table(columns=[{"name": c, "label": c, "field": c} for c in cols], rows=[{
                        "run": r["run"], "params": ", ".join(f"{k} {v}" for k, v in r["params"].items()),
                        "grains": r["n_grains_min_ecd"], "all": r["n_grains_all"],
                        "recFrac %": f"{100 * r['rec_frac']:.1f}", "fit p50": f"{r['fit_quantiles'][2]:.2f}",
                        "fit p90": f"{r['fit_quantiles'][4]:.2f}", "ECDaw": f"{r['ecd_aw_um']:.1f}",
                        "bridged %": f"{r['bridged_pct']:.1f}", "split %": f"{r['split_pct']:.1f}"}
                        for r in t["runs"]]).props("dense flat").mark("sweep_table")
                    with ui.grid(columns=3).classes("w-full"):
                        for k in sorted(o for o in step.outputs if o.startswith("thumb_")):
                            ui.image(str(p.root / step.outputs[k].path))
            ui.button("Run sweep", on_click=sweep).mark("run_sweep")


# ---- Grain size page -------------------------------------------------------------------------------
GS_FIG_KINDS = ["alpha_ecd", "alpha_ecd_area", "lath_thickness", "lath_minor_axis", "colony_ecd", "colony_ecd_area",
                "beta_ecd", "beta_width", "beta_length", "colony_map"]


def _fmt(v, nd=2) -> str:
    return "–" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _grainsize_summary(p: Project, step) -> None:
    """Summary table of a grainsize.run step + links to the CSV / XLSX tables."""
    import json  # noqa: PLC0415

    s = json.loads((p.root / step.outputs["summary"].path).read_text(encoding="utf-8"))

    def st(d, aw=True):
        if not d:
            return "–"
        t = f"mean {_fmt(d['mean'])}, median {_fmt(d['median'])} µm (n = {d['n']})"
        if aw and d.get("area_weighted"):
            t += f"; area-weighted {_fmt(d['area_weighted']['mean'])} µm"
        ln = d.get("lognormal") or {}
        if ln.get("sigma"):
            t += f"; log-normal median {_fmt(ln['median'])}, σ {_fmt(ln['sigma'])}"
        return t
    a, c, b, rb = s["alpha"], s["colony"], s.get("prior_beta"), s["retained_beta"]
    rows = [("backend / rules", f"{s['backend']}; {s['rules']}"),
            ("lath partition", f"grains.calc {s['grains']}: HAGB {s['lath_partition']['hagb_deg']:g}°"),
            ("alpha grain ECD", st(a["ecd"])),
            ("lath thickness, intercept ⊥ long axis", st(a["thickness_intercept"], False)),
            ("lath thickness, minor axis 2b", st(a["thickness_minor_axis"], False)),
            ("colony ECD", st(c["ecd"]) + f" ({c['n_alpha_grains_merged']} alpha grains merged)")]
    if b:
        rows.append(("prior-β ECD", st(b["ecd"])))
        for k, lab in (("across_bd", "prior-β width (across BD)"), ("along_bd", "prior-β length (along BD)")):
            if k in b:
                rows.append((lab, f"E112 mean intercept {_fmt(b[k]['mean_e112_um'], 1)} µm "
                                  f"({b[k]['n_complete']} complete + {b[k]['n_truncated']} edge chords)"))
    rows.append(("retained β (indexed fraction)", f"loaded {100 * rb['loaded']['fraction']:.3f} %, "
                                                  f"analysed map {100 * rb['source_map']['fraction']:.3f} %"))
    rows += [("warning", w) for w in s["warnings"]]
    ui.table(columns=[{"name": "k", "label": "", "field": "k", "align": "left"},
                      {"name": "v", "label": "", "field": "v", "align": "left"}],
             rows=[{"k": k, "v": v} for k, v in rows]).props("dense flat").classes("w-full").mark("gs_summary")
    for k, r in step.outputs.items():
        if r.kind in ("csv", "xlsx"):
            ui.label(f"{k}: {p.root / r.path}").classes("text-xs")


def grainsize_page() -> None:
    _nav()
    p = current_project()
    with ui.column().classes("p-4 gap-3 w-full max-w-6xl"):
        if p is None:
            ui.label("Open a project first.")
            return
        sheets = {s.map_id: s for s in p.samples()}
        if not sheets:
            ui.label("Enter a sample sheet and load a map first.")
            return
        mid = ui.select(list(sheets), value=next(iter(sheets)), label="Map").classes("w-64").mark("gs_map")
        res = ui.column().classes("w-full")
        out = ui.column().classes("w-full")

        async def do(op: str, params: dict, label: str, backend: str | None = None):
            n = ui.notification(f"{label} ...", spinner=True, timeout=None)
            try:
                step = await ng_run.io_bound(service.run, p, op, {"map_id": mid.value, **params}, backend)
                ui.notify(f"{label} done ({step.id}, {step.metrics.get('seconds')} s)", type="positive")
                return step
            except Exception as e:  # noqa: BLE001
                _error(e)
            finally:
                n.dismiss()

        with ui.expansion("1 Alpha grains (laths) and colonies", value=True).classes("w-full"):
            ui.label("Laths = grains of a grains.calc step (threshold below; 15° keeps 10.53° packet pairs in one "
                     "grain, then colonies = alpha grains). Prior β uses the newest reconstruction of the map.").classes("text-sm")
            with ui.row().classes("items-end"):
                hagb = ui.number("lath boundary [°]", value=15.0).classes("w-36").mark("gs_hagb")
                minpx = ui.number("min. pixels", value=5).classes("w-28")
                edge = ui.checkbox("exclude edge grains", value=True)
                asp = ui.number("lath aspect ≥", value=2.0).classes("w-28")
                tol = ui.number("colony tol. [°]", value=3.0).classes("w-28")
                bsp = ui.number("β line spacing [µm]", value=1.0).classes("w-36")

            async def run_gs():
                g = await do("grains.calc", {"grains": {"hagb_deg": hagb.value}}, "grains")
                if not g:
                    return
                step = await do("grainsize.run", {"min_px": int(minpx.value), "exclude_edge": edge.value,
                                                  "lath_min_aspect": asp.value, "colony": {"burgers_tol_deg": tol.value},
                                                  "beta_line_spacing_um": bsp.value}, "grain size")
                if step:
                    res.clear()
                    with res:
                        _grainsize_summary(p, step)
            ui.button("Run grain size", on_click=run_gs).mark("run_gs")
        with ui.expansion("2 Figures", value=True).classes("w-full"):
            fk = ui.select(GS_FIG_KINDS, value="alpha_ecd", label="figure").classes("w-48").mark("gs_fig")

            async def draw():
                s = service.latest_step(p, ("grainsize.run",), map_id=mid.value)
                step = await do("grainsize.figure", {"kind": fk.value}, f"figure {fk.value}",
                                s.backend if s else None)
                if step:
                    out.clear()
                    with out:
                        ui.image(str(p.root / step.outputs["figure"].path)).classes("w-full max-w-4xl")
            ui.button("Draw", on_click=draw).mark("run_gs_fig")
        with ui.expansion("3 Validate against OIM").classes("w-full"):
            ui.label("OIM Analysis grain file export (text, '# Column k: …' header); use the same grain tolerance "
                     "and minimum size in OIM as above.").classes("text-sm")
            with ui.row().classes("items-end"):
                oim = ui.input("OIM grain file").classes("w-96").mark("gs_oim")

                async def compare():
                    s = service.latest_step(p, ("grainsize.run",), map_id=mid.value)
                    step = await do("grainsize.compare_oim", {"file": oim.value}, "OIM comparison",
                                    s.backend if s else None)
                    if step:
                        out.clear()
                        with out:
                            ui.image(str(p.root / step.outputs["comparison"].path)).classes("w-full max-w-3xl")
                ui.button("Compare", on_click=compare)


def build() -> None:
    ui.page("/")(project_page)
    ui.page("/maps")(maps_page)
    ui.page("/recon")(recon_page)
    ui.page("/grainsize")(grainsize_page)
    ui.page("/sample")(sample_page)
    ui.page("/settings")(settings_page)


def main(port: int = 8064, project: str | None = None) -> None:
    STATE["project"] = project
    build()
    ui.run(host="127.0.0.1", port=port, title="Ti64 EBSD/BSE", reload=False, show=False,
           storage_secret=None, favicon="🔬")

