import ast

from mcp import Client

from openapi2mcp.codegen import GenerateOptions, generate_project, resolve_auth
from openapi2mcp.select import Selection, select_operations
from openapi2mcp.spec import AuthHint, parse_spec
from conftest import SPEC


def test_generated_files_and_valid_python(tmp_path):
    parsed = parse_spec(SPEC)
    result = select_operations(parsed.operations, Selection())
    out = tmp_path / "gen"
    generate_project(parsed, result, out, GenerateOptions())
    for name in ("server.py", "mcp_runtime.py", "pyproject.toml", "README.md", ".env.example", ".gitignore"):
        assert (out / name).is_file(), name
    ast.parse((out / "server.py").read_text())
    env = (out / ".env.example").read_text()
    assert "API_BASE_URL=https://api.example.test/v1" in env
    assert "API_KEY=" in env and "API_KEY_NAME=X-Shop-Key" in env
    assert "getItem" in (out / "README.md").read_text()
    assert 'name = "shop-api-mcp"' in (out / "pyproject.toml").read_text()


async def test_tools_listed_with_typed_schemas(make_server):
    mod, _ = make_server()
    async with Client(mod.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"listItems", "createItem", "getItem", "deleteItem", "get_orders_orderId_lines_line-no", "bigList", "emptyCall"}

    schema = tools["listItems"].input_schema
    props = schema["properties"]
    assert props["status"]["anyOf"][0]["enum"] == ["open", "closed"]
    assert "limit" in props and "X-Shop-Key" not in props  # auth header is injected, not a tool arg
    assert "X_Trace_Id" in props
    assert "required" not in schema or "status" not in schema["required"]
    assert "Max rows" in props["limit"]["description"]
    assert "Returns items" in tools["listItems"].description and "Calls GET /items." in tools["listItems"].description

    assert tools["getItem"].input_schema["required"] == ["itemId"]
    assert tools["getItem"].input_schema["properties"]["itemId"]["description"] == "Item id"
    assert tools["createItem"].input_schema["required"] == ["body"]
    line = tools["get_orders_orderId_lines_line-no"].input_schema
    assert line["required"] == ["orderId", "line_no"]
    assert line["properties"]["orderId"]["type"] == "integer"


async def test_annotations_reflect_method(make_server):
    mod, _ = make_server()
    async with Client(mod.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["getItem"].annotations.read_only_hint is True
    assert tools["deleteItem"].annotations.destructive_hint is True
    assert tools["createItem"].annotations.read_only_hint is False


def test_resolve_auth():
    hint = AuthHint("apikey", "X-Shop-Key", "header")
    assert resolve_auth("auto", hint) == hint
    assert resolve_auth("none", hint).mode == "none"
    assert resolve_auth("bearer", hint).mode == "bearer"
    assert resolve_auth("apikey", AuthHint("none")).key_name == "X-API-Key"


def test_bearer_env_example(tmp_path):
    parsed = parse_spec(SPEC)
    result = select_operations(parsed.operations, Selection())
    generate_project(parsed, result, tmp_path, GenerateOptions(auth="bearer"))
    assert "API_TOKEN=" in (tmp_path / ".env.example").read_text()
