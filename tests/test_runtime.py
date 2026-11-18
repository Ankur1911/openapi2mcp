import json

import httpx
import pytest
import respx
from mcp import Client

BASE = "https://api.example.test/v1"


async def call(mod, tool, args=None):
    async with Client(mod.mcp) as client:
        return await client.call_tool(tool, args or {})


def text(result):
    return "".join(c.text for c in result.content)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("API_BASE_URL", "API_TOKEN", "API_KEY", "API_TIMEOUT", "MAX_RESPONSE_CHARS", "API_EXTRA_HEADERS", "API_KEY_NAME", "API_KEY_IN", "API_USER_AGENT"):
        monkeypatch.delenv(k, raising=False)


@respx.mock
async def test_get_with_path_param_and_json_result(make_server):
    mod, _ = make_server()
    route = respx.get(f"{BASE}/items/abc%2F1").mock(return_value=httpx.Response(200, json={"id": "abc/1", "name": "Widget"}))
    result = await call(mod, "getItem", {"itemId": "abc/1"})
    assert route.called
    assert not result.is_error
    assert json.loads(text(result)) == {"id": "abc/1", "name": "Widget"}


@respx.mock
async def test_query_encoding_arrays_booleans_and_headers(make_server):
    mod, _ = make_server()
    route = respx.get(f"{BASE}/items").mock(return_value=httpx.Response(200, json=[]))
    await call(mod, "listItems", {"status": "open", "tag": ["a", "b"], "ids": [1, 2, 3], "limit": 5, "X_Trace_Id": "t-1"})
    req = route.calls.last.request
    assert req.url.params.get_list("tag") == ["a", "b"]  # explode=true -> repeated
    assert req.url.params["ids"] == "1,2,3"  # explode=false -> comma separated
    assert req.url.params["status"] == "open" and req.url.params["limit"] == "5"
    assert req.headers["x-trace-id"] == "t-1"


@respx.mock
async def test_unset_optional_params_are_omitted(make_server):
    mod, _ = make_server()
    route = respx.get(f"{BASE}/items").mock(return_value=httpx.Response(200, json=[]))
    await call(mod, "listItems")
    assert str(route.calls.last.request.url) == f"{BASE}/items"


@respx.mock
async def test_json_body_post(make_server):
    mod, _ = make_server()
    route = respx.post(f"{BASE}/items").mock(return_value=httpx.Response(201, json={"id": 9}))
    result = await call(mod, "createItem", {"body": {"name": "Widget", "price": 2.5}})
    assert json.loads(route.calls.last.request.content) == {"name": "Widget", "price": 2.5}
    assert json.loads(text(result)) == {"id": 9}


@respx.mock
async def test_api_key_header_injected_from_env(make_server, monkeypatch):
    mod, _ = make_server()
    monkeypatch.setenv("API_KEY", "s3cret")
    route = respx.get(f"{BASE}/items/1").mock(return_value=httpx.Response(200, json={}))
    await call(mod, "getItem", {"itemId": "1"})
    assert route.calls.last.request.headers["x-shop-key"] == "s3cret"


@respx.mock
async def test_bearer_token_from_env(make_server, monkeypatch):
    mod, _ = make_server(auth="bearer")
    monkeypatch.setenv("API_TOKEN", "tok123")
    route = respx.get(f"{BASE}/items/1").mock(return_value=httpx.Response(200, json={}))
    await call(mod, "getItem", {"itemId": "1"})
    assert route.calls.last.request.headers["authorization"] == "Bearer tok123"


@respx.mock
async def test_base_url_override(make_server, monkeypatch):
    mod, _ = make_server()
    monkeypatch.setenv("API_BASE_URL", "https://staging.example.test/")
    route = respx.get("https://staging.example.test/items/1").mock(return_value=httpx.Response(200, json={}))
    await call(mod, "getItem", {"itemId": "1"})
    assert route.called


@respx.mock
async def test_404_gives_readable_error(make_server):
    mod, _ = make_server()
    respx.get(f"{BASE}/items/nope").mock(return_value=httpx.Response(404, json={"error": "no such item"}))
    result = await call(mod, "getItem", {"itemId": "nope"})
    assert result.is_error
    msg = text(result)
    assert "HTTP 404" in msg and "GET /items/{itemId}" in msg and "no such item" in msg


@respx.mock
async def test_401_mentions_credentials(make_server):
    mod, _ = make_server(auth="bearer")
    respx.get(f"{BASE}/items/1").mock(return_value=httpx.Response(401, text="unauthorized"))
    result = await call(mod, "getItem", {"itemId": "1"})
    assert result.is_error and "API_TOKEN" in text(result)


@respx.mock
async def test_429_mentions_retry_after(make_server):
    mod, _ = make_server()
    respx.get(f"{BASE}/items/1").mock(return_value=httpx.Response(429, headers={"retry-after": "7"}, text="slow down"))
    result = await call(mod, "getItem", {"itemId": "1"})
    assert result.is_error and "retry after 7s" in text(result)


@respx.mock
async def test_timeout_and_connect_errors(make_server):
    mod, _ = make_server()
    respx.get(f"{BASE}/items/t").mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(f"{BASE}/items/c").mock(side_effect=httpx.ConnectError("refused"))
    r1 = await call(mod, "getItem", {"itemId": "t"})
    r2 = await call(mod, "getItem", {"itemId": "c"})
    assert r1.is_error and "timed out" in text(r1) and "API_TIMEOUT" in text(r1)
    assert r2.is_error and "could not connect" in text(r2)


@respx.mock
async def test_204_no_content(make_server):
    mod, _ = make_server()
    respx.get(f"{BASE}/empty").mock(return_value=httpx.Response(204))
    result = await call(mod, "emptyCall")
    assert "no content" in text(result)


@respx.mock
async def test_large_list_is_truncated_but_stays_valid_json(make_server, monkeypatch):
    mod, _ = make_server()
    monkeypatch.setenv("MAX_RESPONSE_CHARS", "2000")
    payload = [{"id": i, "name": "x" * 40} for i in range(500)]
    respx.get(f"{BASE}/big").mock(return_value=httpx.Response(200, json=payload))
    out = text(await call(mod, "bigList"))
    body, _, note = out.partition("\n\n[truncated")
    assert len(out) <= 2100
    shown = json.loads(body)
    assert 0 < len(shown) < 500 and shown == payload[: len(shown)]
    assert "of 500" in note


@respx.mock
async def test_large_wrapped_list_truncates_the_inner_list(make_server, monkeypatch):
    mod, _ = make_server()
    monkeypatch.setenv("MAX_RESPONSE_CHARS", "1500")
    payload = {"total": 300, "rows": [{"id": i, "pad": "y" * 30} for i in range(300)]}
    respx.get(f"{BASE}/big").mock(return_value=httpx.Response(200, json=payload))
    out = text(await call(mod, "bigList"))
    body, _, note = out.partition("\n\n[truncated")
    data = json.loads(body)
    assert data["total"] == 300 and 0 < len(data["rows"]) < 300
    assert "entries of 'rows'" in note


@respx.mock
async def test_large_plain_text_is_cut(make_server, monkeypatch):
    mod, _ = make_server()
    monkeypatch.setenv("MAX_RESPONSE_CHARS", "1000")
    respx.get(f"{BASE}/big").mock(return_value=httpx.Response(200, text="z" * 50000, headers={"content-type": "text/plain"}))
    out = text(await call(mod, "bigList"))
    assert len(out) <= 1000 and "truncated" in out


@respx.mock
async def test_binary_response_is_summarised(make_server):
    mod, _ = make_server()
    respx.get(f"{BASE}/big").mock(return_value=httpx.Response(200, content=b"\x00\x01" * 10, headers={"content-type": "image/png"}))
    assert "binary response" in text(await call(mod, "bigList"))
