import json

import pytest
import yaml

from conftest import SPEC, SWAGGER2
from openapi2mcp.spec import SpecError, load_document, parse_spec, parse_text, sanitize_identifier


def ops_by_name(parsed):
    return {o.tool_name: o for o in parsed.operations}


def test_basic_metadata(parsed):
    assert parsed.title == "Shop API"
    assert parsed.version == "2.1.0"
    assert parsed.base_url == "https://api.example.test/v1"  # server variable default substituted
    assert len(parsed.operations) == 8


def test_operation_ids_and_fallback_names(parsed):
    names = set(ops_by_name(parsed))
    assert {"listItems", "createItem", "getItem", "deleteItem", "oldThing"} <= names
    # operation without operationId gets a safe generated name
    assert "get_orders_orderId_lines_line-no" in names


def test_path_level_ref_param_is_merged(parsed):
    op = ops_by_name(parsed)["listItems"]
    limit = next(p for p in op.params if p.name == "limit")
    assert limit.py_type == "int"
    assert "Max rows" in limit.description and "default: 10" in limit.description


def test_param_types(parsed):
    op = ops_by_name(parsed)["listItems"]
    by = {p.name: p for p in op.params}
    assert by["status"].py_type == "Literal['open', 'closed']"
    assert by["tag"].py_type == "list[str]" and by["tag"].explode
    assert by["ids"].py_type == "list[int]" and not by["ids"].explode
    assert "session" not in by  # cookie params unsupported
    assert any("cookie" in s for s in parsed.skipped_params)


def test_path_params_required_first(parsed):
    op = ops_by_name(parsed)["get_orders_orderId_lines_line-no"]
    assert [p.location for p in op.params][:2] == ["path", "path"]
    assert all(p.required for p in op.params[:2])
    by = {p.name: p for p in op.params}
    assert by["line-no"].py_name == "line_no"
    assert by["class"].py_name == "class_"  # python keyword


def test_request_body(parsed):
    op = ops_by_name(parsed)["createItem"]
    assert op.body and op.body.required
    assert "name*" in op.body.description  # required marker in the field hint


def test_auth_hint(parsed):
    assert parsed.auth_hint.mode == "apikey"
    assert parsed.auth_hint.key_name == "X-Shop-Key"
    assert parsed.auth_hint.key_in == "header"


def test_whitespace_cleaned(parsed):
    assert ops_by_name(parsed)["listItems"].description == "Returns items\nfrom the catalogue."


def test_swagger2():
    parsed = parse_spec(SWAGGER2)
    assert parsed.base_url == "https://old.example.test/api"
    ops = ops_by_name(parsed)
    assert ops["getThing"].params[0].py_type == "int"
    fields = next(p for p in ops["getThing"].params if p.name == "fields")
    assert fields.explode and fields.is_array
    assert ops["putThing"].body is not None and ops["putThing"].body.required
    assert parsed.auth_hint.mode == "apikey" and parsed.auth_hint.key_in == "query"


def test_yaml_and_json_parse():
    assert parse_text(json.dumps(SPEC))["info"]["title"] == "Shop API"
    assert parse_text(yaml.safe_dump(SPEC))["info"]["title"] == "Shop API"


def test_invalid_documents():
    with pytest.raises(SpecError):
        parse_text("[1, 2]")
    with pytest.raises(SpecError):
        parse_text('{"info": {}}')
    with pytest.raises(SpecError):
        parse_text('{"openapi": "3.0.0"}')


def test_load_from_file(tmp_path):
    f = tmp_path / "spec.yaml"
    f.write_text(yaml.safe_dump(SPEC))
    doc, origin = load_document(str(f))
    assert origin is None and doc["info"]["title"] == "Shop API"
    with pytest.raises(SpecError):
        load_document(str(tmp_path / "missing.json"))


def test_circular_ref_does_not_hang():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "components": {"schemas": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}}},
        "paths": {"/n": {"get": {"parameters": [{"name": "q", "in": "query", "schema": {"$ref": "#/components/schemas/Node"}}]}}},
    }
    op = parse_spec(doc).operations[0]
    assert op.params[0].py_type == "dict[str, Any]"


def test_sanitize_identifier():
    used: set[str] = set()
    assert sanitize_identifier("user-id", used) == "user_id"
    assert sanitize_identifier("user_id", used) == "user_id_2"
    assert sanitize_identifier("1st", None) == "p_1st"
    assert sanitize_identifier("filter[name]", None) == "filter_name"
