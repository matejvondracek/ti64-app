"""Phase 6: every service operation as an MCP tool with typed inputs, resources, `ti64 run recipe.json`,
compare-backends helpers. MCP calls go through the real protocol (in-memory client) where it matters."""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.client import Client

from ti64app import cli, mcp_server, service
from ti64app.ops_compare import compare_rows, default_figure, flatten_numbers, montage
from ti64app.project import Project, projects_dir

from tests.conftest import sample_params

GENERIC_PARAMS = {("run_operation", "params"), ("compare_backends", "params")}   # any op's params, validated there


def _tools():
    return {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}


def test_every_operation_has_a_tool():
    tools = _tools()
    assert set(mcp_server.TOOL_FOR_OP) == set(service.OPS)
    assert set(mcp_server.TOOL_FOR_OP.values()) <= set(tools)
    assert {"compare_backends", "run_recipe", "run_operation", "list_operations", "get_step", "verify_outputs",
            "set_figure_settings"} <= set(tools)


def _untyped(schema: dict, defs: dict) -> bool:
    """True for a bare object (dict[str, Any]) without properties or typed values."""
    if "$ref" in schema:
        return _untyped(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return any(_untyped(s, defs) for s in schema["anyOf"])
    return schema.get("type") == "object" and not schema.get("properties") and \
        schema.get("additionalProperties", True) in (True, {})


def test_tool_inputs_are_typed_and_documented():
    for name, t in _tools().items():
        sch = t.input_schema
        assert t.description and len(t.description) > 20, name
        for prop, ps in sch.get("properties", {}).items():
            if (name, prop) not in GENERIC_PARAMS:
                assert not _untyped(ps, sch.get("$defs", {})), f"{name}.{prop} is an untyped dict"
    # typed nested inputs carry the service-layer defaults
    rb = _tools()["reconstruct_beta"].input_schema["$defs"]["ReconParams"]["properties"]
    assert rb["vgThreshold"]["default"] == 5.0 and rb["mergeThreshold"]["default"] == 3.0


def test_list_operations_matches_catalog():
    ops = mcp_server.list_operations()
    assert set(ops) == set(service.OPS)
    assert ops["recon.run"]["tool"] == "reconstruct_beta" and ops["recon.run"]["uses_backend"]


def _call(client_fn):
    async def go():
        async with Client(mcp_server.server, raise_exceptions=True) as c:
            return await client_fn(c)
    return asyncio.run(go())


def _payload(res):
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(res.content[0].text)


def test_mcp_protocol_chain_and_resources(crop300_ju02):
    """sample sheet -> load -> cleanup (typed overrides) -> grains through the MCP protocol; resources read back."""
    async def chain(c):
        r = _payload(await c.call_tool("set_sample_sheet", {
            "project": "mp", "map_file": str(crop300_ju02), "specimen_id": "EBMX-1", "input_state": "oim_cleaned",
            "build_direction_angle_deg": 270, "map_id": "JU02_c300"}))
        assert r["map_id"] == "JU02_c300"
        load = _payload(await c.call_tool("load_map", {"project": "mp", "map_id": "JU02_c300"}))
        assert load["meta"]["n"] == 90000 and load["meta"]["grid"] == "hex"
        cl = _payload(await c.call_tool("cleanup_map", {"project": "mp", "map_id": "JU02_c300",
                                                        "overrides": {"noise_angle_deg": 2.5}}))
        assert cl["report"]["params"]["noise_angle_deg"] == 2.5
        assert cl["report"]["params"]["noise_max_void_px"] == 4          # untouched preset value
        bad = await c.call_tool("cleanup_map", {"project": "mp", "map_id": "JU02_c300",
                                                "overrides": {"noise_angle_deg": "lots"}})
        assert bad.is_error
        g = _payload(await c.call_tool("calc_grains", {"project": "mp", "map_id": "JU02_c300", "hagb_deg": 10}))
        assert g["summary"]
        res = await c.list_resource_templates()
        assert {"ti64://projects/{project}/recipe"} <= {r.uri_template for r in res.resource_templates}
        rec = json.loads((await c.read_resource("ti64://projects/mp/recipe")).contents[0].text)
        state = json.loads((await c.read_resource("ti64://projects/mp")).contents[0].text)
        sheet = json.loads((await c.read_resource("ti64://projects/mp/samples/JU02_c300")).contents[0].text)
        step = json.loads((await c.read_resource(f"ti64://projects/mp/steps/{cl['step_id']}")).contents[0].text)
        ops = json.loads((await c.read_resource("ti64://operations")).contents[0].text)
        return rec, state, sheet, step, ops, g
    rec, state, sheet, step, ops, g = _call(chain)
    assert [s["op"] for s in rec["steps"]] == ["sample.set", "ebsd.load", "ebsd.cleanup", "grains.calc"]
    assert rec["steps"][3]["params"]["grains"]["hagb_deg"] == 10
    assert state["backend"] == "pymtex" and len(state["steps"]) == 4 and "undo" in state
    assert sheet["build_direction"]["angle_deg"] == 270
    assert step["json"]["report"]["n_points"] == 90000
    assert "compare.backends" in ops


def test_unknown_project_is_an_error_not_created():
    with pytest.raises(ValueError, match="no project"):
        mcp_server.project_summary("does_not_exist")
    assert not (projects_dir() / "does_not_exist").exists()


def test_set_figure_settings_partial():
    out = mcp_server.set_figure_settings(mcp_server.FigurePatch(dpi=300))
    assert out["figure"]["dpi"] == 300 and out["figure"]["colormap_kam"] == "turbo"
    assert mcp_server.get_settings()["figure"]["dpi"] == 300


# ---- ti64 run recipe.json ---------------------------------------------------------------------------
def _small_project(crop300_ju02) -> Project:
    p = Project.create("src")
    service.run(p, "sample.set", sample_params(crop300_ju02))
    service.run(p, "ebsd.load", {"map_id": "JU02_c300"})
    service.run(p, "ebsd.cleanup", {"map_id": "JU02_c300"})
    return p


def test_cli_run_project_recipe_reproduces(crop300_ju02, capsys):
    src = _small_project(crop300_ju02)
    rc = cli.main(["run", str(src.recipe_path), "--project", "copy", "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["ok"]
    dst = Project.open(projects_dir() / "copy")
    assert [s.id for s in dst.recipe().steps] == [s.id for s in src.recipe().steps]
    assert {c["status"] for c in rep["checks"]} <= {"identical", "within_tolerance"}
    # same step ids again into the same project: refused
    with pytest.raises(ValueError, match="already in project"):
        cli.main(["run", str(src.recipe_path), "--project", "copy"])


def test_cli_run_handwritten_recipe(crop300_ju02, tmp_path, monkeypatch, capsys):
    """Hand-written list of {op, params}: relative map path (resolved against the working directory and recorded
    absolute), no ids, no backend (project backend), defaults resolve to the newest steps."""
    monkeypatch.chdir(crop300_ju02.parent)
    f = tmp_path / "demo.json"
    sp = sample_params(crop300_ju02)
    sp["map_file"] = crop300_ju02.name
    f.write_text(json.dumps([
        {"op": "sample.set", "params": sp},
        {"op": "ebsd.load", "params": {"map_id": "JU02_c300"}},
        {"op": "ebsd.cleanup", "params": {"map_id": "JU02_c300", "overrides": {"noise_angle_deg": 2.0}}},
        {"op": "figure", "params": {"map_id": "JU02_c300", "kind": "ipf"}},
    ]), encoding="utf-8")
    assert cli.main(["run", str(f), "--project", "hw"]) == 0
    out = capsys.readouterr().out
    assert "ebsd.cleanup" in out and "project:" in out
    p = Project.open(projects_dir() / "hw")
    steps = p.recipe().steps
    assert [s.op for s in steps] == ["sample.set", "ebsd.load", "ebsd.cleanup", "figure"]
    assert steps[0].params["map_file"] == str(crop300_ju02.absolute())
    assert steps[2].backend == "pymtex" and steps[3].params["source"] == steps[2].id
    assert (p.root / steps[3].outputs["figure"].path).is_file()
    assert service.replay(p).ok


def test_mcp_run_recipe_and_replay_report(crop300_ju02):
    src = _small_project(crop300_ju02)
    out = mcp_server.run_recipe(str(src.recipe_path), "via_mcp")
    assert out["ok"] and len(out["steps"]) == 3
    rep = mcp_server.replay_project("src")
    assert rep["ok"] and rep["replay_root"]


def test_cli_ops_lists_every_operation(capsys):
    assert cli.main(["ops"]) == 0
    out = capsys.readouterr().out
    assert all(n in out for n in service.OPS)


# ---- compare-backends helpers -------------------------------------------------------------------------
def test_flatten_and_compare_rows():
    a = flatten_numbers({"report": {"n": 10, "frac": 0.5, "ok": True, "name": "x", "q": [1, 2], "long": list(range(50))}})
    assert a == {"report.n": 10, "report.frac": 0.5, "report.ok": True, "report.name": "x",
                 "report.q[0]": 1, "report.q[1]": 2}
    b = {**a, "report.frac": 0.51, "report.name": "y", "report.extra": 3}
    rows = {r["key"]: r for r in compare_rows(a, b)}
    assert rows["report.n"]["same"] and rows["report.n"]["rel_diff"] == 0
    assert rows["report.frac"]["abs_diff"] == pytest.approx(0.01)
    assert rows["report.frac"]["rel_diff"] == pytest.approx(0.01 / 0.51)
    assert not rows["report.name"]["same"] and rows["report.name"]["rel_diff"] is None
    assert rows["report.extra"]["a"] is None and not rows["report.extra"]["same"]


def test_montage_keeps_pixels(tmp_path):
    from PIL import Image

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (40, 30), "red").save(a)
    Image.new("RGB", (50, 20), "blue").save(b)
    out = montage([("pymtex", a), ("matlab", b)], tmp_path / "m.png")
    im = Image.open(out)
    assert im.width >= 90 and im.height > 30
    assert im.getpixel((10, im.height - 5)) == (255, 0, 0)


def test_default_figures():
    from ti64app.recipe import RecipeStep

    s = RecipeStep(id="r1", op="recon.run", params={"map_id": "m"})
    assert default_figure("recon.run", s, None) == ("recon.figure", {"map_id": "m", "kind": "parent_ipf", "recon": "r1"})
    assert default_figure("recon.run", s, "variant")[1]["kind"] == "variant"
    assert default_figure("export.map", s, None) is None


def test_compare_backends_validation(crop300_ju02):
    p = _small_project(crop300_ju02)
    with pytest.raises(ValueError, match="backend operation"):
        service.run(p, "compare.backends", {"op": "sample.set", "params": {}})
    with pytest.raises(ValueError, match="two different"):
        service.run(p, "compare.backends", {"op": "ebsd.cleanup", "params": {"map_id": "JU02_c300"},
                                            "backends": ["pymtex", "pymtex"]})
