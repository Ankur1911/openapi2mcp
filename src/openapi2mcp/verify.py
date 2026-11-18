"""Start a generated server over stdio and talk to it with the MCP client."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters


async def verify_server(
    server_dir: Path,
    *,
    use_uv: bool = False,
    call: str | None = None,
    call_args: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Launch server.py in server_dir, list its tools, optionally call one."""
    server_dir = server_dir.resolve()
    if use_uv:
        params = StdioServerParameters(command="uv", args=["run", "--quiet", "server.py"], cwd=server_dir, env=env)
    else:
        params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=server_dir, env=env)
    report: dict[str, Any] = {}
    async with Client(params) as client:
        info = client.server_info
        report["server"] = info.name if info else None
        tools = (await client.list_tools()).tools
        report["tools"] = [{"name": t.name, "description": (t.description or "").split("\n")[0], "params": sorted((t.input_schema or {}).get("properties", {}))} for t in tools]
        if call:
            result = await client.call_tool(call, call_args or {})
            text = "".join(getattr(c, "text", "") for c in result.content)
            report["call"] = {"tool": call, "is_error": bool(result.is_error), "preview": text[:600], "length": len(text)}
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [f"server: {report.get('server')}", f"tools: {len(report['tools'])}"]
    for t in report["tools"]:
        lines.append(f"  - {t['name']}({', '.join(t['params'])}): {t['description']}")
    if "call" in report:
        c = report["call"]
        lines.append(f"call {c['tool']}: is_error={c['is_error']} length={c['length']}")
        lines.append("  " + c["preview"].replace("\n", "\n  "))
    return "\n".join(lines)


def parse_args_json(text: str | None) -> dict[str, Any]:
    return json.loads(text) if text else {}
