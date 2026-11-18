"""Render a standalone MCP server project from selected operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from . import __version__
from .select import Selection, SelectionResult
from .spec import AuthHint, Operation, ParsedSpec

MAX_DESC_CHARS = 1200


@dataclass
class GenerateOptions:
    auth: str = "auto"  # auto | bearer | apikey | none
    server_name: str | None = None
    base_url: str | None = None  # override the base URL taken from the spec


def resolve_auth(requested: str, hint: AuthHint) -> AuthHint:
    if requested == "auto":
        return hint
    if requested == "none":
        return AuthHint("none")
    if requested == "bearer":
        return AuthHint("bearer")
    if requested == "apikey":
        if hint.mode == "apikey":
            return hint
        return AuthHint("apikey", "X-API-Key", "header")
    raise ValueError(f"Unknown auth mode: {requested}")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "api"


def tool_description(op: Operation) -> str:
    parts: list[str] = []
    if op.summary:
        parts.append(op.summary)
    if op.description and op.description != op.summary:
        text = op.description
        if len(text) > MAX_DESC_CHARS:
            text = text[:MAX_DESC_CHARS].rstrip() + " ..."
        parts.append(text)
    parts.append(f"Calls {op.method} {op.path}.")
    return "\n\n".join(parts)


def _visible_params(op: Operation, auth: AuthHint):
    """Params the model should fill in; the auth credential is injected by the runtime."""
    for p in op.params:
        if auth.mode == "apikey" and p.location == auth.key_in and (
            p.name == auth.key_name or (p.location == "header" and p.name.lower() == auth.key_name.lower())
        ):
            continue
        yield p


def _op_table(op: Operation, params) -> dict:
    return {
        "method": op.method,
        "path": op.path,
        "params": [
            {"name": p.name, "py": p.py_name, "in": p.location, "explode": p.explode} for p in params
        ],
        "body": {"py": op.body.py_name} if op.body else None,
    }


def _field_desc(text: str) -> str:
    return repr(text) if text else "''"


def _render_tool(index: int, op: Operation, params: list) -> str:
    sig: list[str] = []
    for p in params:
        t = p.py_type if p.required else f"{p.py_type} | None"
        ann = f"Annotated[{t}, Field(description={_field_desc(p.description)})]"
        sig.append(f"    {p.py_name}: {ann}" + ("" if p.required else " = None"))
    if op.body:
        b = op.body
        t = "dict[str, Any] | list[Any]" if b.required else "dict[str, Any] | list[Any] | None"
        desc = ("JSON request body. " + b.description).strip()
        sig.append(f"    {b.py_name}: Annotated[{t}, Field(description={_field_desc(desc)})]" + ("" if b.required else " = None"))
        # keep required-before-optional ordering valid
        sig.sort(key=lambda s: s.endswith("= None"))
    args = [p.py_name for p in params] + ([op.body.py_name] if op.body else [])
    call_args = "{" + ", ".join(f'"{a}": {a}' for a in args) + "}"

    read_only = op.method in ("GET", "HEAD", "OPTIONS")
    ann_kwargs = [f"read_only_hint={read_only}", "open_world_hint=True"]
    if op.method == "DELETE":
        ann_kwargs.append("destructive_hint=True")
    if op.method in ("GET", "PUT", "DELETE", "HEAD"):
        ann_kwargs.append("idempotent_hint=True")
    fn_name = f"tool_{index:03d}_" + re.sub(r"\W", "_", op.tool_name)
    sig_src = ",\n".join(sig)
    return (
        f"@mcp.tool(\n"
        f"    name={op.tool_name!r},\n"
        f"    description={tool_description(op)!r},\n"
        f"    annotations=ToolAnnotations({', '.join(ann_kwargs)}),\n"
        f"    structured_output=False,\n"
        f")\n"
        f"async def {fn_name}({chr(10) + sig_src + chr(10) if sig else ''}) -> str:\n"
        f"    return await api.call(OPERATIONS[{op.tool_name!r}], {call_args})\n"
    )


def render_server(spec: ParsedSpec, ops: list[Operation], opts: GenerateOptions) -> str:
    auth = resolve_auth(opts.auth, spec.auth_hint)
    base_url = opts.base_url if opts.base_url is not None else spec.base_url
    name = opts.server_name or slugify(spec.title)
    tools: list[str] = []
    table: dict[str, dict] = {}
    for i, op in enumerate(ops, 1):
        params = list(_visible_params(op, auth))
        table[op.tool_name] = _op_table(op, params)
        tools.append(_render_tool(i, op, params))

    instructions = f"Tools for the {spec.title} HTTP API"
    if spec.version:
        instructions += f" (version {spec.version})"
    instructions += f". {len(ops)} operation(s) exposed. Responses are JSON text and may be truncated; narrow requests with filters when you see a truncation notice."

    ops_literal = "".join(f"    {k!r}: {v!r},\n" for k, v in table.items())
    header = f'''"""MCP server for the {spec.title} API.

Generated by openapi2mcp {__version__}. Run with:  uv run server.py
Configuration comes from environment variables or a .env file next to this file
(see .env.example).
"""

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_runtime import ApiClient, load_env_file, setup_logging

load_env_file(Path(__file__).with_name(".env"))

SERVER_NAME = {name!r}
DEFAULT_BASE_URL = {base_url!r}

OPERATIONS = {{
{ops_literal}}}

mcp = MCPServer(SERVER_NAME, instructions={instructions!r})
api = ApiClient(DEFAULT_BASE_URL, auth_mode={auth.mode!r}, key_name={auth.key_name!r}, key_in={auth.key_in!r})

'''
    footer = '''
def main() -> None:
    setup_logging()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(
            transport,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()
'''
    return header + "\n\n".join(tools) + "\n" + footer


def render_env_example(spec: ParsedSpec, opts: GenerateOptions) -> str:
    auth = resolve_auth(opts.auth, spec.auth_hint)
    base_url = opts.base_url if opts.base_url is not None else spec.base_url
    lines = [
        "# Copy to .env and fill in. Real environment variables take priority over this file.",
        f"API_BASE_URL={base_url}",
    ]
    if auth.mode == "bearer":
        lines += ["# Bearer token sent as 'Authorization: Bearer <token>'", "API_TOKEN="]
    elif auth.mode == "apikey":
        lines += [
            f"# API key sent in the '{auth.key_name}' {auth.key_in}",
            "API_KEY=",
            f"API_KEY_NAME={auth.key_name}",
            f"API_KEY_IN={auth.key_in}",
        ]
    else:
        lines += ["# No credentials configured (generated with --auth none or no security scheme in the spec)."]
    lines += [
        "",
        "# Seconds to wait for a response (default 30) and for a connection (default 10)",
        "API_TIMEOUT=30",
        "# Responses longer than this many characters are truncated with a notice",
        "MAX_RESPONSE_CHARS=20000",
        "# Optional: custom User-Agent, and extra headers as a JSON object",
        "# API_USER_AGENT=my-demo/1.0",
        '# API_EXTRA_HEADERS={"X-Tenant": "demo"}',
        "LOG_LEVEL=INFO",
        "",
        "# Transport: stdio (default, for MCP clients that launch the process) or streamable-http",
        "# MCP_TRANSPORT=stdio",
        "# MCP_HOST=127.0.0.1",
        "# MCP_PORT=8000",
        "",
    ]
    return "\n".join(lines)


def render_pyproject(spec: ParsedSpec, opts: GenerateOptions) -> str:
    name = opts.server_name or slugify(spec.title)
    return f'''[project]
name = "{slugify(name)}-mcp"
version = "0.1.0"
description = "MCP server for the {spec.title.replace('"', "'")} API"
requires-python = ">=3.12"
dependencies = [
    "mcp>=2.3.0,<3",
    "httpx>=0.28.1",
]

# Run with `uv run server.py`; this is an application, not an installable package.
[tool.uv]
package = false
'''


def render_readme(spec: ParsedSpec, ops: list[Operation], opts: GenerateOptions, result: SelectionResult | None) -> str:
    auth = resolve_auth(opts.auth, spec.auth_hint)
    name = opts.server_name or slugify(spec.title)
    rows = "\n".join(f"| `{o.tool_name}` | `{o.method} {o.path}` | {o.summary or ''} |" for o in ops)
    auth_note = {
        "bearer": "Set `API_TOKEN` in `.env`; it is sent as a bearer token.",
        "apikey": f"Set `API_KEY` in `.env`; it is sent in the `{auth.key_name}` {auth.key_in}.",
        "none": "No credentials are configured.",
    }[auth.mode]
    capped = ""
    if result and result.dropped_by_cap:
        capped = f"\n{len(result.dropped_by_cap)} further matching operation(s) were left out by the tool cap.\n"
    return f"""# {spec.title} MCP server

Generated by openapi2mcp {__version__} from the {spec.title} OpenAPI spec ({len(ops)} tools).

## Run

```bash
cp .env.example .env     # then edit if the API needs credentials
uv run server.py         # speaks MCP over stdio
```

{auth_note}

## Connect a client

Add this to any MCP-compatible client (for example Cursor or VS Code), using the absolute path to this folder:

```json
{{
  "mcpServers": {{
    "{name}": {{
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/this/folder", "server.py"]
    }}
  }}
}}
```

The exact config file and key names differ by client; check your client's MCP documentation.

## Tools

| Tool | Request | Summary |
|---|---|---|
{rows}
{capped}
## Configuration

See `.env.example`. Key settings: `API_BASE_URL`, `API_TIMEOUT`, `MAX_RESPONSE_CHARS`, `LOG_LEVEL`, `MCP_TRANSPORT`.

Logs go to stderr (stdout is reserved for the protocol).
"""


def generate_project(
    spec: ParsedSpec,
    result: SelectionResult,
    out_dir: Path,
    opts: GenerateOptions,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ops = result.selected
    runtime_src = resources.files("openapi2mcp").joinpath("templates/mcp_runtime.py").read_text(encoding="utf-8")
    files = {
        "server.py": render_server(spec, ops, opts),
        "mcp_runtime.py": runtime_src,
        "pyproject.toml": render_pyproject(spec, opts),
        "README.md": render_readme(spec, ops, opts, result),
        ".env.example": render_env_example(spec, opts),
        ".gitignore": ".env\n.venv/\n__pycache__/\n",
    }
    written = []
    for fname, content in files.items():
        path = out_dir / fname
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


__all__ = ["GenerateOptions", "generate_project", "render_server", "Selection"]
