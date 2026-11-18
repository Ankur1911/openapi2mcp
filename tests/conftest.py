import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from openapi2mcp.codegen import GenerateOptions, generate_project
from openapi2mcp.select import Selection, select_operations
from openapi2mcp.spec import parse_spec

BASE = "https://api.example.test/v1"

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Shop API", "version": "2.1.0", "description": "A demo shop."},
    "servers": [{"url": "https://{env}.example.test/v1", "variables": {"env": {"default": "api"}}}],
    "components": {
        "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Shop-Key"}},
        "parameters": {"Limit": {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10}, "description": "Max rows"}},
        "schemas": {
            "NewItem": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "price": {"type": "number"}}},
            "Status": {"type": "string", "enum": ["open", "closed"]},
        },
    },
    "paths": {
        "/items": {
            "parameters": [{"$ref": "#/components/parameters/Limit"}],
            "get": {
                "operationId": "listItems",
                "tags": ["items"],
                "summary": "List items",
                "description": "Returns   items\nfrom the catalogue.",
                "parameters": [
                    {"name": "status", "in": "query", "schema": {"$ref": "#/components/schemas/Status"}},
                    {"name": "tag", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                    {"name": "ids", "in": "query", "explode": False, "schema": {"type": "array", "items": {"type": "integer"}}},
                    {"name": "X-Shop-Key", "in": "header", "schema": {"type": "string"}},
                    {"name": "X-Trace-Id", "in": "header", "schema": {"type": "string"}},
                    {"name": "session", "in": "cookie", "schema": {"type": "string"}},
                ],
            },
            "post": {
                "operationId": "createItem",
                "tags": ["items"],
                "summary": "Create item",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewItem"}}}},
            },
        },
        "/items/{itemId}": {
            "get": {
                "operationId": "getItem",
                "tags": ["items"],
                "summary": "Get one item",
                "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Item id"}],
            },
            "delete": {
                "operationId": "deleteItem",
                "tags": ["items"],
                "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
            },
        },
        "/orders/{orderId}/lines/{line-no}": {
            "get": {
                "tags": ["orders"],
                "summary": "Order line",
                "parameters": [
                    {"name": "orderId", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "line-no", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "class", "in": "query", "schema": {"type": "string"}},
                ],
            }
        },
        "/legacy": {"get": {"operationId": "oldThing", "deprecated": True, "tags": ["orders"], "summary": "Old"}},
        "/big": {"get": {"operationId": "bigList", "tags": ["orders"], "summary": "Large payload"}},
        "/empty": {"get": {"operationId": "emptyCall", "tags": ["orders"], "summary": "No content"}},
    },
}

SWAGGER2 = {
    "swagger": "2.0",
    "info": {"title": "Old API", "version": "1"},
    "host": "old.example.test",
    "basePath": "/api",
    "schemes": ["https"],
    "securityDefinitions": {"k": {"type": "apiKey", "in": "query", "name": "token"}},
    "paths": {
        "/things/{id}": {
            "get": {
                "operationId": "getThing",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "type": "integer"},
                    {"name": "fields", "in": "query", "type": "array", "items": {"type": "string"}, "collectionFormat": "multi"},
                ],
            },
            "put": {
                "operationId": "putThing",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "type": "integer"},
                    {"name": "body", "in": "body", "required": True, "schema": {"type": "object", "properties": {"a": {"type": "string"}}}},
                ],
            },
        }
    },
}


@pytest.fixture
def spec_doc():
    return SPEC


@pytest.fixture
def parsed():
    return parse_spec(SPEC)


def load_generated(out_dir: Path):
    """Import a generated server.py and return the module."""
    sys.path.insert(0, str(out_dir))
    try:
        name = f"gen_server_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, out_dir / "server.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(out_dir))


@pytest.fixture
def make_server(tmp_path):
    def _make(methods=("GET", "POST", "DELETE"), auth="auto", **sel):
        parsed = parse_spec(SPEC)
        result = select_operations(parsed.operations, Selection(methods=list(methods), **sel))
        out = tmp_path / "srv"
        generate_project(parsed, result, out, GenerateOptions(auth=auth))
        return load_generated(out), out

    return _make
