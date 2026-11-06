"""Phase 6.6 GUI (NiceGUI simulated user, Windows dialogs mocked): home -> new project -> Files tab (add files,
samples, auto-assign, sheets with inheritance, autosave) -> EBSD tab (load, cleanup Apply, drafts, history undo) ->
Settings dialog -> Stitch tab. The browser click-through (Compare to original, export) is in
tests/integration/test_gui_playwright.py."""
import shutil
import sys

import pytest
from nicegui.testing.user_simulation import user_simulation

from tests.conftest import sample_params
from ti64app import service
from ti64app.gui import app as gui
from ti64app.gui import dialogs
from ti64app.project import Project, projects_dir

from .test_gui_pages import MAIN


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_state():
    gui.STATE["project"] = None
    dialogs.MOCK = []
    yield
    gui.STATE["project"] = None
    dialogs.MOCK = None


def _open(ref):
    sys.modules["ti64app.gui.app"].STATE["project"] = str(ref)


@pytest.mark.anyio
async def test_home_new_project_dialog(tmp_path):
    dialogs.MOCK[:] = [str(tmp_path)]                     # folder dialog of "Location: Browse…"
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/")
        await user.should_see("Ti64 EBSD/BSE")
        await user.should_see("Prior-β reconstruction")
        user.find(marker="home_new").click()
        await user.should_see(marker="np_name")
        user.find(marker="np_name").type("guiP")
        user.find(marker="np_browse").click()
        await user.should_see(f"Creates {tmp_path / 'guiP'}")
        user.find(marker="np_create").click()
        await user.should_see(marker="tab_files")
        await user.should_see("No files yet")
    p = Project.open(tmp_path / "guiP")
    assert p.info().name == "guiP" and p.files() == []


@pytest.mark.anyio
async def test_files_tab_samples_inheritance_autosave(tmp_path, crop300_ju02):
    p = Project.create("gf", base=tmp_path)
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    img = img_dir / "9445_MVH1_JU04_SED_1.bmp"
    from PIL import Image

    Image.new("L", (64, 48)).save(img)
    (img_dir / "9445_MVH1_JU04_SED_1.txt").write_text("$CM_FORMAT JEOL/SEM\n", encoding="utf-8")
    dialogs.MOCK[:] = [[str(crop300_ju02), str(img), str(img.with_suffix(".txt"))]]
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        await user.should_see("No files yet")
        user.find(marker="add_file").click()
        await user.should_see("2 file(s) linked")
        await user.should_see("Unassigned")
        # auto-assign: JU02 from the .ang name, JU04 from the image name (confirm the list)
        user.find(marker="auto_assign").click()
        await user.should_see("Auto-assign files to samples")
        user.find(marker="aa_confirm").click()
        await user.should_see("2 file(s) assigned")
        await user.should_see(marker="sample_JU02")
        await user.should_see(marker="sample_JU04")
        # sample sheet: heat treatment, inherited by the files
        user.find(marker="sample_JU02").click()
        await user.should_see(marker="sheet_sample")
        user.find(marker="ss_heat_treatment").type("HIP").trigger("blur")
        mid = next(m.id for m in Project.open(p.root).files() if m.kind == "ang")
        user.find(marker=f"file_{mid}").click()
        await user.should_see(marker="sheet_map")
        await user.should_see("from sample")
        await user.should_see("suggested from the name: OIM-cleaned")
        user.find(marker="ms_input_state").elements.pop().set_value("oim_cleaned")
        user.find(marker="ms_bd_angle").type("270").trigger("blur")
        await user.should_see(marker="file_meta")
        await user.should_see("hexagonal")
    q = Project.open(p.root)
    s = q.sample(mid)
    assert s.specimen_id == "JU02" and s.input_state == "oim_cleaned" and s.build_direction.angle_deg == 270
    assert q.sample_info("JU02").heat_treatment == "HIP"
    assert {m.sample_id for m in q.files()} == {"JU02", "JU04"} and len(q.files()) == 2      # .txt hidden


@pytest.mark.anyio
async def test_files_tab_missing_file_relocate_and_remove(tmp_path, crop300_ju02):
    """Find missing: choose a FOLDER, it is searched recursively by name (and size) for all missing files, the matches
    are listed and relinked at once; the list updates immediately (no second click)."""
    p = Project.create("gm", base=tmp_path)
    ang = tmp_path / "moved.ang"
    gone = tmp_path / "gone.ang"
    shutil.copy(crop300_ju02, ang)
    shutil.copy(crop300_ju02, gone)
    m, m2 = p.add_files([ang, gone])
    ang.unlink()
    gone.unlink()
    (tmp_path / "archive" / "deep").mkdir(parents=True)
    new = tmp_path / "archive" / "deep" / "moved.ang"
    shutil.copy(crop300_ju02, new)
    dialogs.MOCK[:] = [str(tmp_path / "archive")]
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        await user.should_see(marker=f"relocate_{m.id}")
        user.find(marker=f"relocate_{m.id}").click()
        await user.should_see(marker="relocate_dialog")
        await user.should_see("Missing files found in")
        await user.should_see("not found in this folder")
        user.find(marker="reloc_go").click()
        await user.should_see("1 file(s) relinked")
        await user.should_not_see(marker=f"relocate_{m.id}")          # the row updated right away
        await user.should_see(marker=f"relocate_{m2.id}")
        assert dialogs.CALLS[-1]["kind"] == "folder"
        user.find(marker=f"file_{m.id}").click()
        user.find(marker="remove_file").click()
        await user.should_see("The file stays on disk")
        user.find(marker="confirm_ok").click()
        await user.should_not_see(marker=f"file_{m.id}")
    files = Project.open(p.root).files()
    assert [f.id for f in files] == [m2.id] and new.is_file()


@pytest.mark.anyio
async def test_files_tab_new_sample_for_file_fold_and_add_filter(tmp_path, crop300_ju02):
    p = Project.create("gn", base=tmp_path)
    ang = tmp_path / "EBM_JU07_map.ang"
    shutil.copy(crop300_ju02, ang)
    dialogs.MOCK[:] = [[str(ang)]]
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        user.find(marker="add_file").click()
        await user.should_see("1 file(s) linked")
        assert dialogs.CALLS[-1]["kind"] == "open" and dialogs.CALLS[-1]["all_first"] is True
        m = Project.open(p.root).files()[0]
        user.find(marker=f"newsample_{m.id}").click()          # right-click menu: New sample for this file
        await user.should_see(marker="new_sample_dialog")
        assert user.find(marker="ns_id").elements.pop().value == "JU07"     # prefilled from the file name
        user.find(marker="ns_create").click()
        await user.should_see("→ new sample JU07")
        assert Project.open(p.root).file(m.id).sample_id == "JU07"
        await user.should_see(marker="name_sample_JU07")
        user.find(marker="name_sample_JU07").click()            # click the sample name: fold its files
        await user.should_not_see(marker=f"file_{m.id}")
        assert Project.open(p.root).gui_state()["files_collapsed"] == ["JU07"]
    async with user_simulation(main_file=MAIN) as user:        # remembered
        _open(p.root)
        await user.open("/files")
        await user.should_see(marker="sample_JU07")
        await user.should_not_see(marker=f"file_{m.id}")
        user.find(marker="fold_sample_JU07").click()            # the chevron unfolds again
        await user.should_see(marker=f"file_{m.id}")


@pytest.mark.anyio
async def test_ebsd_tab_load_cleanup_drafts_undo(tmp_path, crop300_ju02):
    p = Project.create("ge", base=tmp_path)
    service.run(p, "sample.set", sample_params(crop300_ju02))
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/ebsd")
        await user.should_see(marker="edit_menu")
        await user.should_see("is not loaded yet", retries=100)
        user.find(marker="run_load").click()
        await user.should_see("load map done", retries=300)
        await user.should_see(marker="map_viewer", retries=200)
        user.find(marker="cleanup_noise_max_void_px").elements.pop().set_value(6)
        await user.should_see("not applied")
        user.find(marker="run_cleanup").click()
        await user.should_see("cleanup done", retries=300)
    steps = Project.open(p.root).recipe().steps
    assert [s.op for s in steps] == ["sample.set", "ebsd.load", "ebsd.cleanup"]
    assert steps[-1].params["overrides"]["noise_max_void_px"] == 6 and steps[-1].sample_id == "EBMX-1"
    assert (Project.open(p.root).find_step_dir(steps[-1].id)).name.startswith("EBMX-1_")
    assert Project.open(p.root).gui_state().get("drafts", {}).get("cleanup") is None
    # a draft that was typed but not applied survives reopening
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/ebsd")
        user.find(marker="grains_hagb_deg").elements.pop().set_value(12)
    assert Project.open(p.root).gui_state()["drafts"]["grains"]["hagb_deg"] == 12
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/ebsd")
        await user.should_see("● not applied")
        await user.should_see(marker="history_panel")             # History: the panel right of the map
        await user.should_see(marker="recipe_table")
        user.find(marker="undo").click()
        await user.should_see("undone: ebsd.cleanup", retries=100)
    assert [s.op for s in Project.open(p.root).recipe().steps] == ["sample.set", "ebsd.load"]


@pytest.mark.anyio
async def test_settings_dialog_autosaves(tmp_path):
    from ti64app.settings import load_settings

    p = Project.create("gs", base=tmp_path)
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        user.find(marker="settings_gear").click()
        await user.should_see(marker="settings_dialog")
        for c in ("settings_backend", "settings_ipf", "settings_style", "settings_paths", "settings_disk"):
            await user.should_see(marker=c)
        await user.should_see("pymtex: available")
        user.find("literature_dir").clear().type("C:/elsewhere/literature").trigger("blur")
        user.find(marker="clear_cache").click()
        await user.should_see("Cache cleared")
    assert load_settings().paths.literature_dir == "C:/elsewhere/literature"


@pytest.mark.anyio
async def test_interrupted_step_is_offered(tmp_path):
    from ti64app.project import write_json

    p = Project.create("gi", base=tmp_path)
    write_json(p.root / "running.json", {"op": "up2.header", "params": {"file": "x.up2"}, "pid": 999999,
                                         "pid_started": 0, "started": "2026-11-06T10:00:00"})
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/files")
        await user.should_see("A step was interrupted")
        user.find(marker="interrupted_dismiss").click()
    assert Project.open(p.root).interrupted() is None


@pytest.mark.anyio
async def test_crack_placeholder_and_home_redirect(tmp_path):
    p = Project.create("gc", base=tmp_path)
    async with user_simulation(main_file=MAIN) as user:
        _open(p.root)
        await user.open("/crack")
        await user.should_see(marker="crack_placeholder")
        await user.should_see("phase 8")
    assert projects_dir()
