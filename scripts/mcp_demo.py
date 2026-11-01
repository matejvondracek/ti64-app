"""Phase 6 demo: load map -> cleanup -> prior-beta reconstruction -> export, entirely through MCP tools over stdio,
on both backends, plus compare_backends. Launches the same command that is registered in Claude Code (`ti64 mcp`).

    python scripts/mcp_demo.py <map.ang> --angle 270 [--prefix demo_mcp] [--server <path to ti64.exe>]

The build direction (--angle, deg from map +x toward +y) and the input state must come from the user's sample sheet.
Projects <prefix>_pymtex and <prefix>_matlab are created in projects/ (git-ignored); a JSON log of every call is
written to projects/<prefix>_log.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

REPO = Path(__file__).resolve().parents[1]


def payload(res) -> dict:
    if res.is_error:
        raise RuntimeError(res.content[0].text if res.content else "tool error")
    sc = getattr(res, "structured_content", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


async def demo(a) -> list[dict]:
    log: list[dict] = []
    server = a.server or shutil.which("ti64") or str(Path(sys.executable).parent / "Scripts" / "ti64.exe")
    params = StdioServerParameters(command=server, args=["mcp"], cwd=str(REPO))
    async with Client(params, read_timeout_seconds=1800) as c:
        async def call(tool: str, **args):
            t0 = time.perf_counter()
            out = payload(await c.call_tool(tool, args))
            dt = time.perf_counter() - t0
            log.append({"tool": tool, "args": args, "seconds": round(dt, 1), "result": out})
            print(f"  {tool:<18} {dt:7.1f} s", file=sys.stderr, flush=True)
            return out

        tools = await c.list_tools()
        print(f"server {c.server_info.name} {c.server_info.version}: {len(tools.tools)} tools", file=sys.stderr)
        print(f"backends: {await call('list_backends')}", file=sys.stderr)
        for backend in ("pymtex", "matlab"):
            proj = f"{a.prefix}_{backend}"
            print(f"--- {backend} -> projects/{proj}", file=sys.stderr)
            await call("create_project", name=proj, backend=backend)
            sheet = await call("set_sample_sheet", project=proj, map_file=str(Path(a.map).resolve()),
                               specimen_id=a.specimen, input_state=a.input_state,
                               build_direction_angle_deg=a.angle, map_id=a.map_id)
            mid = sheet["map_id"]
            await call("load_map", project=proj, map_id=mid)
            await call("cleanup_map", project=proj, map_id=mid)
            await call("reconstruct_beta", project=proj, map_id=mid)        # method default: loaded map (D12)
            await call("recon_figure", project=proj, map_id=mid, kind="parent_ipf")
            await call("export_map", project=proj, map_id=mid, kind="parent_ipf", overlays=["beta_gb"],
                       mode="width", width_mm=120, dpi=300, format="png", backend=backend)
            await call("export_map", project=proj, map_id=mid, kind="ipf", mode="native", format="png",
                       layer={"direction": "build"})          # cleaned alpha map, one pixel per point, IPF || BD
            res = await c.read_resource(f"ti64://projects/{proj}/recipe")
            steps = json.loads(res.contents[0].text)["steps"]
            log.append({"resource": f"ti64://projects/{proj}/recipe",
                        "steps": [(s["id"], s["op"], s["backend"]) for s in steps]})
        await call("compare_backends", project=f"{a.prefix}_pymtex", op="recon.run",
                   params={"map_id": mid}, figure_kind="parent_ipf")
    return log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--angle", type=float, required=True, help="build direction, deg from map +x toward +y")
    ap.add_argument("--input-state", default="oim_cleaned", choices=["raw", "oim_cleaned"])
    ap.add_argument("--specimen", default="EBMX-1")
    ap.add_argument("--map-id")
    ap.add_argument("--prefix", default="demo_mcp")
    ap.add_argument("--server")
    a = ap.parse_args()
    log = asyncio.run(demo(a))
    out = REPO / "projects" / f"{a.prefix}_log.json"
    out.write_text(json.dumps(log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"log: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
