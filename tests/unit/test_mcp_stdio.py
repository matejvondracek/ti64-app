"""The stdio MCP server must survive stray output: Python print() in cp1252 (pymtex prints the OR scan with a
degree sign), raw writes to fd 1 and child processes writing to their inherited stdout."""
from __future__ import annotations

import asyncio
import sys
import textwrap

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

NOISY_SERVER = textwrap.dedent('''
    import os, subprocess, sys
    from ti64app import mcp_server

    @mcp_server.server.tool()
    def noisy() -> str:
        """Writes to every stdout path (test only)."""
        print("(124.96\\u00b0, 44.32\\u00b0)  1.327")
        sys.stdout.buffer.write(b"\\xb0 raw cp1252 bytes\\n"); sys.stdout.flush()
        os.write(1, b"\\xb0 fd1\\n")
        subprocess.run([sys.executable, "-c", "print('child \\u00b0')"], check=True)
        return "ok"

    mcp_server.main()
''')


def test_stray_stdout_never_reaches_the_wire(tmp_path):
    script = tmp_path / "noisy_server.py"
    script.write_text(NOISY_SERVER, encoding="utf-8")

    async def go():
        params = StdioServerParameters(command=sys.executable, args=[str(script)])
        async with Client(params, read_timeout_seconds=120) as c:
            r1 = await c.call_tool("noisy", {})
            r2 = await c.call_tool("list_backends", {})
            return r1, r2
    r1, r2 = asyncio.run(go())
    assert not r1.is_error and "ok" in r1.content[0].text
    assert not r2.is_error and "pymtex" in r2.content[0].text
