"""Load and normalise an OpenAPI 3.x / Swagger 2.0 document into plain dataclasses."""

from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
MAX_DEPTH = 6


class SpecError(Exception):
    """Raised when a spec cannot be loaded or understood."""


@dataclass
class Param:
    name: str  # name on the wire
    py_name: str  # python identifier used in the generated tool signature
    location: str  # path | query | header
    required: bool
    py_type: str  # python type source, e.g. "int", "list[str]", "Literal['a', 'b']"
    description: str = ""
    explode: bool = True
    is_array: bool = False


@dataclass
class Body:
    py_name: str
    required: bool
    content_type: str
    description: str = ""


@dataclass
class Operation:
    operation_id: str
    tool_name: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    body: Body | None = None
    deprecated: bool = False


@dataclass
class AuthHint:
    mode: str = "none"  # none | bearer | apikey
    key_name: str = "X-API-Key"
    key_in: str = "header"  # header | query


@dataclass
class ParsedSpec:
    title: str
    version: str
    description: str
    base_url: str
    operations: list[Operation]
    auth_hint: AuthHint
    skipped_params: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- loading


def load_document(source: str, timeout: float = 30.0) -> tuple[dict[str, Any], str | None]:
    """Read a spec from a URL or a local path. Returns (document, origin_url_or_None)."""
    if re.match(r"^https?://", source):
        try:
            resp = httpx.get(
                source,
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "openapi2mcp", "Accept": "application/json, application/yaml, */*"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpecError(f"Could not download spec from {source}: {exc}") from exc
        text, origin = resp.text, str(resp.url)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise SpecError(f"Spec file not found: {source}")
        text, origin = path.read_text(encoding="utf-8"), None
    return parse_text(text), origin


def parse_text(text: str) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SpecError(f"Spec is neither valid JSON nor YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise SpecError("Spec root must be an object.")
    if "openapi" not in doc and "swagger" not in doc:
        raise SpecError("Document has neither an 'openapi' nor a 'swagger' version field.")
    if not isinstance(doc.get("paths"), dict):
        raise SpecError("Document has no 'paths' object.")
    return doc


# --------------------------------------------------------------------------- helpers


def sanitize_identifier(name: str, used: set[str] | None = None) -> str:
    ident = re.sub(r"\W", "_", name).strip("_") or "param"
    if ident[0].isdigit():
        ident = f"p_{ident}"
    if keyword.iskeyword(ident) or ident in {"self", "mcp", "api"}:
        ident += "_"
    if used is not None:
        base, n = ident, 2
        while ident in used:
            ident = f"{base}_{n}"
            n += 1
        used.add(ident)
    return ident


def _tool_name(op_id: str | None, method: str, path: str) -> str:
    raw = op_id or f"{method}_{path}"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_.-")
    return re.sub(r"_+", "_", name or f"{method}_op")[:64]


def _clean(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()


class _Resolver:
    def __init__(self, doc: dict[str, Any]):
        self.doc = doc

    def resolve(self, node: Any, _seen: frozenset[str] = frozenset()) -> Any:
        """Follow local $ref chains (not recursive into children)."""
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in _seen:
                return {}
            _seen = _seen | {ref}
            target: Any = self.doc
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    return {}
                target = target[part]
            node = target
        return node

    def type_of(self, schema: Any, depth: int = 0) -> tuple[str, bool, str]:
        """Return (python type source, is_array, extra description) for a schema."""
        schema = self.resolve(schema)
        if not isinstance(schema, dict) or depth > MAX_DEPTH:
            return "Any", False, ""
        for key in ("allOf", "oneOf", "anyOf"):
            subs = schema.get(key)
            if isinstance(subs, list):
                resolved = [self.resolve(s) for s in subs]
                non_null = [s for s in resolved if isinstance(s, dict) and s.get("type") != "null"]
                if key == "allOf" and len(non_null) == 1:
                    return self.type_of(non_null[0], depth + 1)
                if key != "allOf" and len(non_null) == 1:
                    return self.type_of(non_null[0], depth + 1)
                return "Any", False, ""
        stype = schema.get("type")
        if isinstance(stype, list):
            stype = next((t for t in stype if t != "null"), None)
        enum = schema.get("enum")
        if isinstance(enum, list) and enum and len(enum) <= 50 and all(
            isinstance(v, (str, int)) and not isinstance(v, bool) for v in enum
        ):
            return "Literal[" + ", ".join(repr(v) for v in enum) + "]", False, ""
        if stype == "string":
            return "str", False, ""
        if stype == "integer":
            return "int", False, ""
        if stype == "number":
            return "float", False, ""
        if stype == "boolean":
            return "bool", False, ""
        if stype == "array":
            inner, _, _ = self.type_of(schema.get("items", {}), depth + 1)
            return f"list[{inner}]", True, ""
        if stype == "object" or "properties" in schema:
            return "dict[str, Any]", False, ""
        return "Any", False, ""

    def body_hint(self, schema: Any) -> str:
        schema = self.resolve(schema)
        if not isinstance(schema, dict):
            return ""
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            required = set(schema.get("required") or [])
            parts = []
            for name, sub in list(props.items())[:25]:
                sub = self.resolve(sub)
                t = sub.get("type", "any") if isinstance(sub, dict) else "any"
                if isinstance(t, list):
                    t = "/".join(str(x) for x in t)
                parts.append(f"{name}{'*' if name in required else ''} ({t})")
            return "Fields (* = required): " + ", ".join(parts)
        if schema.get("type") == "array":
            return "A JSON array."
        return ""


# --------------------------------------------------------------------------- parsing


def _base_url(doc: dict[str, Any], origin: str | None) -> str:
    if "swagger" in doc:
        host = doc.get("host")
        if host:
            scheme = (doc.get("schemes") or ["https"])[0]
            return f"{scheme}://{host}{doc.get('basePath', '') or ''}".rstrip("/")
        base = (doc.get("basePath") or "").rstrip("/")
        return urljoin(origin, base) if origin and base else base
    servers = doc.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", ""))
        for var, spec in (servers[0].get("variables") or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                url = url.replace("{" + var + "}", str(spec["default"]))
        if origin and not urlparse(url).scheme:
            url = urljoin(origin, url)
        return url.rstrip("/")
    if origin:
        p = urlparse(origin)
        return f"{p.scheme}://{p.netloc}"
    return ""


def _auth_hint(doc: dict[str, Any], res: _Resolver) -> AuthHint:
    schemes = doc.get("components", {}).get("securitySchemes") or doc.get("securityDefinitions") or {}
    for raw in schemes.values():
        s = res.resolve(raw)
        if not isinstance(s, dict):
            continue
        stype = s.get("type")
        if stype == "http" and str(s.get("scheme", "")).lower() == "bearer":
            return AuthHint("bearer")
        if stype in ("oauth2", "openIdConnect"):
            return AuthHint("bearer")
        if stype == "apiKey" and s.get("in") in ("header", "query"):
            return AuthHint("apikey", str(s.get("name", "X-API-Key")), s["in"])
    return AuthHint("none")


def _parse_params(
    raw_params: list[Any], res: _Resolver, swagger2: bool
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Resolve parameter objects. Returns (non-body params, swagger2 body param or None)."""
    out: list[dict[str, Any]] = []
    body = None
    for raw in raw_params:
        p = res.resolve(raw)
        if not isinstance(p, dict) or "name" not in p:
            continue
        if swagger2 and p.get("in") == "body":
            body = p
            continue
        out.append(p)
    return out, body


def parse_spec(doc: dict[str, Any], origin: str | None = None) -> ParsedSpec:
    res = _Resolver(doc)
    swagger2 = "swagger" in doc
    info = doc.get("info") or {}
    auth_hint = _auth_hint(doc, res)
    ops: list[Operation] = []
    used_tool_names: set[str] = set()
    skipped: list[str] = []

    for path, item in doc["paths"].items():
        item = res.resolve(item)
        if not isinstance(item, dict):
            continue
        path_params, _ = _parse_params(item.get("parameters") or [], res, swagger2)
        for method in HTTP_METHODS:
            raw_op = item.get(method)
            if not isinstance(raw_op, dict):
                continue
            op_params, sw_body = _parse_params(raw_op.get("parameters") or [], res, swagger2)
            merged: dict[tuple[str, str], dict[str, Any]] = {}
            for p in path_params + op_params:
                merged[(p["name"], p.get("in", "query"))] = p

            used: set[str] = set()
            params: list[Param] = []
            for (name, loc), p in merged.items():
                if loc not in ("path", "query", "header"):
                    skipped.append(f"{method.upper()} {path}: {loc} parameter '{name}'")
                    continue
                if loc == "header" and name.lower() in ("authorization", "content-type", "accept"):
                    continue
                schema = p.get("schema") if "schema" in p else {
                    k: p[k] for k in ("type", "format", "enum", "items") if k in p
                }
                py_type, is_array, _ = res.type_of(schema)
                explode = p.get("explode", p.get("style", "form") == "form") if not swagger2 else (
                    p.get("collectionFormat", "csv") == "multi"
                )
                rs = res.resolve(schema)
                rs = rs if isinstance(rs, dict) else {}
                default = rs.get("default")
                desc = _clean(p.get("description")) or _clean(rs.get("description"))
                if default is not None:
                    desc = f"{desc} (default: {default})".strip()
                params.append(
                    Param(
                        name=name,
                        py_name=sanitize_identifier(name, used),
                        location=loc,
                        required=bool(p.get("required")) or loc == "path",
                        py_type=py_type,
                        description=desc,
                        explode=bool(explode),
                        is_array=is_array,
                    )
                )
            params.sort(key=lambda x: not x.required)  # required first (stable)

            body: Body | None = None
            if swagger2:
                if sw_body is not None:
                    hint = res.body_hint(sw_body.get("schema"))
                    desc = " ".join(x for x in (_clean(sw_body.get("description")), hint) if x)
                    body = Body(sanitize_identifier("body", used), bool(sw_body.get("required")), "application/json", desc)
            else:
                rb = res.resolve(raw_op.get("requestBody"))
                if isinstance(rb, dict) and rb.get("content"):
                    content = rb["content"]
                    ctype = next((c for c in content if "json" in c), None)
                    if ctype is None:
                        skipped.append(f"{method.upper()} {path}: non-JSON request body ({', '.join(content)})")
                    else:
                        hint = res.body_hint((content[ctype] or {}).get("schema"))
                        desc = " ".join(x for x in (_clean(rb.get("description")), hint) if x)
                        body = Body(sanitize_identifier("body", used), bool(rb.get("required")), ctype, desc)

            name = _tool_name(raw_op.get("operationId"), method, path)
            base, n = name, 2
            while name in used_tool_names:
                suffix = f"_{n}"
                name = base[: 64 - len(suffix)] + suffix
                n += 1
            used_tool_names.add(name)

            ops.append(
                Operation(
                    operation_id=raw_op.get("operationId") or name,
                    tool_name=name,
                    method=method.upper(),
                    path=path,
                    summary=_clean(raw_op.get("summary")),
                    description=_clean(raw_op.get("description")),
                    tags=[str(t) for t in raw_op.get("tags") or []],
                    params=params,
                    body=body,
                    deprecated=bool(raw_op.get("deprecated")),
                )
            )

    return ParsedSpec(
        title=_clean(info.get("title")) or "API",
        version=str(info.get("version", "")),
        description=_clean(info.get("description")),
        base_url=_base_url(doc, origin),
        operations=ops,
        auth_hint=auth_hint,
        skipped_params=skipped,
    )


def load_spec(source: str) -> ParsedSpec:
    doc, origin = load_document(source)
    return parse_spec(doc, origin)
