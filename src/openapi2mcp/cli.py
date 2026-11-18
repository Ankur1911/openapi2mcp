"""Command line interface: generate, serve, list, verify."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from .codegen import GenerateOptions, generate_project
from .select import Selection, select_operations
from .spec import SpecError, load_spec


def _csv(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out.extend(x.strip() for x in v.split(",") if x.strip())
    return out


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("spec", help="OpenAPI spec URL or local file path (JSON or YAML; OpenAPI 3.x or Swagger 2.0)")
    p.add_argument("--include-tags", nargs="+", metavar="TAG", help="Only operations with one of these tags")
    p.add_argument("--exclude-tags", nargs="+", metavar="TAG", help="Skip operations with any of these tags")
    p.add_argument("--include-paths", nargs="+", metavar="GLOB", help="Only paths matching these globs, e.g. '/pet/*'")
    p.add_argument("--exclude-paths", nargs="+", metavar="GLOB", help="Skip paths matching these globs")
    p.add_argument(
        "--methods",
        nargs="+",
        default=["GET"],
        metavar="METHOD",
        help="HTTP methods to expose (default: GET only, for safety; add POST PUT PATCH DELETE deliberately)",
    )
    p.add_argument("--include-deprecated", action="store_true", help="Also expose deprecated operations")
    p.add_argument("--max-tools", type=int, default=40, help="Cap on tool count (default 40, 0 = no cap)")
    p.add_argument(
        "--auth",
        choices=["bearer", "apikey", "none", "auto"],
        default="auto",
        help="Credential style read from env vars (default auto: follow the spec's security scheme)",
    )
    p.add_argument("--base-url", help="Override the API base URL from the spec")
    p.add_argument("--name", help="Server name (default: derived from the spec title)")


def _selection(args: argparse.Namespace) -> Selection:
    return Selection(
        include_tags=_csv(args.include_tags),
        exclude_tags=_csv(args.exclude_tags),
        include_paths=_csv(args.include_paths),
        exclude_paths=_csv(args.exclude_paths),
        methods=_csv(args.methods),
        include_deprecated=args.include_deprecated,
        max_tools=args.max_tools if args.max_tools > 0 else None,
    )


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _prepare(args: argparse.Namespace):
    spec = load_spec(args.spec)
    result = select_operations(spec.operations, _selection(args))
    if not result.selected:
        raise SpecError(
            f"No operations matched your filters ({len(spec.operations)} in the spec). "
            "Check --methods (default GET only), --include-tags and --include-paths."
        )
    opts = GenerateOptions(auth=args.auth, server_name=args.name, base_url=args.base_url)
    return spec, result, opts


def _summary(spec, result) -> None:
    _log(f"{spec.title} {spec.version}: {len(spec.operations)} operations in spec, {len(result.selected)} selected as tools")
    if result.dropped_by_cap:
        _log(f"  {len(result.dropped_by_cap)} more matched but were cut by --max-tools; raise it or narrow with --include-tags")
    if spec.skipped_params:
        _log(f"  note: {len(spec.skipped_params)} spec element(s) not mapped (cookie params or non-JSON request bodies)")


def cmd_generate(args: argparse.Namespace) -> int:
    spec, result, opts = _prepare(args)
    out = Path(args.out)
    written = generate_project(spec, result, out, opts)
    _summary(spec, result)
    _log(f"Wrote {len(written)} files to {out}")
    _log(f"Next: cd {out} && cp .env.example .env && uv run server.py")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    spec, result, opts = _prepare(args)
    _summary(spec, result)
    with tempfile.TemporaryDirectory(prefix="openapi2mcp-") as tmp:
        generate_project(spec, result, Path(tmp), opts)
        env = None
        if args.transport != "stdio":
            import os

            env = {**os.environ, "MCP_TRANSPORT": args.transport, "MCP_HOST": args.host, "MCP_PORT": str(args.port)}
        # The generated server runs in this interpreter, so it has mcp and httpx already.
        return subprocess.call([sys.executable, str(Path(tmp) / "server.py")], cwd=tmp, env=env)


def cmd_list(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    result = select_operations(spec.operations, _selection(args))
    print(f"{spec.title} {spec.version}  base URL: {spec.base_url or '(none in spec)'}  auth hint: {spec.auth_hint.mode}")
    print(f"{len(spec.operations)} operations in spec, {result.candidates} match filters, {len(result.selected)} would become tools\n")
    for op in result.selected:
        tags = ",".join(op.tags)
        print(f"  {op.tool_name:<40} {op.method:<6} {op.path}  [{tags}]")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    import asyncio

    from .verify import format_report, parse_args_json, verify_server

    report = asyncio.run(
        verify_server(Path(args.server_dir), use_uv=args.use_uv, call=args.call, call_args=parse_args_json(args.args))
    )
    print(format_report(report))
    return 1 if report.get("call", {}).get("is_error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openapi2mcp", description="Turn an OpenAPI spec into an MCP server.")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="Generate a standalone MCP server project")
    _add_common(g)
    g.add_argument("--out", required=True, help="Output directory")
    g.set_defaults(func=cmd_generate)

    s = sub.add_parser("serve", help="Serve a spec directly over MCP without keeping generated files")
    _add_common(s)
    s.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    ls = sub.add_parser("list", help="Preview which operations would become tools")
    _add_common(ls)
    ls.set_defaults(func=cmd_list)

    v = sub.add_parser("verify", help="Start a generated server over stdio, list its tools, optionally call one")
    v.add_argument("server_dir", help="Directory containing a generated server.py")
    v.add_argument("--use-uv", action="store_true", help="Launch with `uv run server.py` (proves the project is standalone)")
    v.add_argument("--call", metavar="TOOL", help="Also call this tool")
    v.add_argument("--args", metavar="JSON", help="JSON object of arguments for --call")
    v.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        _log(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
