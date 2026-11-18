import asyncio
import json

from openapi2mcp.cli import main
from openapi2mcp.verify import verify_server
from conftest import SPEC


def test_generate_cli_and_stdio_verify(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    out = tmp_path / "out"
    code = main(["generate", str(spec_file), "--out", str(out), "--include-tags", "items", "--max-tools", "5"])
    assert code == 0
    assert (out / "server.py").is_file()
    # real stdio round trip: spawn the generated server and list tools with the MCP client
    report = asyncio.run(verify_server(out))
    assert {t["name"] for t in report["tools"]} == {"listItems", "getItem"}


def test_list_cli(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    assert main(["list", str(spec_file), "--methods", "GET", "POST"]) == 0
    out = capsys.readouterr().out
    assert "createItem" in out and "deleteItem" not in out


def test_no_match_is_a_clean_error(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))
    assert main(["generate", str(spec_file), "--out", str(tmp_path / "o"), "--include-tags", "nothing"]) == 2
    assert "No operations matched" in capsys.readouterr().err


def test_serve_mode_over_stdio(tmp_path):
    import sys

    from mcp import Client, StdioServerParameters

    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(SPEC))

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "openapi2mcp.cli", "serve", str(spec_file), "--include-tags", "items"])
        async with Client(params) as client:
            return sorted(t.name for t in (await client.list_tools()).tools)

    assert asyncio.run(run()) == ["getItem", "listItems"]
