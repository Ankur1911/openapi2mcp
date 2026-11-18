"""Runtime helpers for a generated MCP server: HTTP calls, auth, errors, truncation.

This file is copied next to server.py so the generated project stands alone.
Everything is configured through environment variables (see .env.example).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver.exceptions import ToolError

log = logging.getLogger("mcp_api")

DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_MAX_CHARS = 20000


def load_env_file(path: Path) -> None:
    """Minimal .env loader. Real environment variables always win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


def setup_logging() -> None:
    # stdout carries the MCP protocol over stdio, so logs must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full URLs at INFO, which would leak API keys sent as query parameters.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def shrink(text: str, limit: int) -> str:
    """Cut a response down to ~limit characters, keeping valid JSON where possible."""
    if len(text) <= limit:
        return text
    note = (
        f"\n\n[truncated: the full response was {len(text)} characters. "
        "Narrow the request with filter or limit parameters to see the rest.]"
    )
    budget = max(limit - len(note) - 80, 200)

    def dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    try:
        data = json.loads(text)
    except ValueError:
        data = None

    holder: tuple[Any, str | None] | None = None
    if isinstance(data, list):
        holder = (data, None)
    elif isinstance(data, dict):
        lists = {k: v for k, v in data.items() if isinstance(v, list) and v}
        if lists:
            key = max(lists, key=lambda k: len(dumps(lists[k])))
            holder = (data, key)

    if holder is not None:
        obj, key = holder
        items = obj if key is None else obj[key]

        def build(k: int) -> str:
            return dumps(items[:k]) if key is None else dumps({**obj, key: items[:k]})

        lo, hi = 0, len(items)
        while lo < hi:  # largest k that fits
            mid = (lo + hi + 1) // 2
            if len(build(mid)) <= budget:
                lo = mid
            else:
                hi = mid - 1
        if lo >= 1:
            label = "items" if key is None else f"entries of '{key}'"
            return build(lo) + f"\n\n[truncated: showing {lo} of {len(items)} {label}; full response was {len(text)} characters. Narrow the request with filter or limit parameters to see the rest.]"
    return text[:budget] + note


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class ApiClient:
    def __init__(self, default_base_url: str, auth_mode: str = "none", key_name: str = "X-API-Key", key_in: str = "header"):
        self.default_base_url = default_base_url
        self.auth_mode = auth_mode
        self.key_name = key_name
        self.key_in = key_in

    # Settings are read per call so edits to the environment take effect without code changes.
    @property
    def base_url(self) -> str:
        return os.environ.get("API_BASE_URL", self.default_base_url).rstrip("/")

    def _auth(self, headers: dict[str, str], query: dict[str, Any]) -> None:
        if self.auth_mode == "bearer":
            token = os.environ.get("API_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif self.auth_mode == "apikey":
            key = os.environ.get("API_KEY")
            if key:
                name = os.environ.get("API_KEY_NAME", self.key_name)
                where = os.environ.get("API_KEY_IN", self.key_in)
                (query if where == "query" else headers)[name] = key

    def _auth_hint(self) -> str:
        if self.auth_mode == "bearer":
            return " Check that API_TOKEN is set and valid."
        if self.auth_mode == "apikey":
            return " Check that API_KEY is set and valid."
        return " This server was generated without auth; regenerate with --auth bearer or --auth apikey if the API needs credentials."

    async def call(self, op: dict[str, Any], args: dict[str, Any]) -> str:
        base = self.base_url
        if not base:
            raise ToolError("No API base URL is configured. Set API_BASE_URL.")

        path = op["path"]
        query: dict[str, Any] = {}
        headers: dict[str, str] = {
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.5",
            "User-Agent": os.environ.get("API_USER_AGENT", "openapi2mcp-server"),
        }
        extra = os.environ.get("API_EXTRA_HEADERS")
        if extra:
            try:
                headers.update({str(k): str(v) for k, v in json.loads(extra).items()})
            except (ValueError, AttributeError):
                log.warning("API_EXTRA_HEADERS is not a JSON object; ignoring it")

        for p in op["params"]:
            value = args.get(p["py"])
            if value is None:
                continue
            if p["in"] == "path":
                path = path.replace("{" + p["name"] + "}", quote(_scalar(value), safe=""))
            elif p["in"] == "header":
                headers[p["name"]] = _scalar(value)
            elif isinstance(value, list):
                items = [_scalar(v) for v in value]
                query[p["name"]] = items if p["explode"] else ",".join(items)
            else:
                query[p["name"]] = _scalar(value)
        self._auth(headers, query)

        body = None
        if op.get("body") and args.get(op["body"]["py"]) is not None:
            body = args[op["body"]["py"]]

        timeout = httpx.Timeout(
            _float_env("API_TIMEOUT", DEFAULT_TIMEOUT),
            connect=_float_env("API_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
        )
        label = f"{op['method']} {op['path']}"
        log.info("request %s", label)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.request(
                    op["method"], base + path, params=query or None, headers=headers, json=body
                )
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"{label} timed out after {timeout.read}s ({type(exc).__name__}). "
                "The API may be slow or down; retry, or raise API_TIMEOUT."
            ) from exc
        except httpx.ConnectError as exc:
            raise ToolError(f"{label}: could not connect to {base}. Check API_BASE_URL and network access.") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"{label} failed: {type(exc).__name__}: {exc}") from exc

        log.info("response %s -> %s", label, resp.status_code)
        limit = int(_float_env("MAX_RESPONSE_CHARS", DEFAULT_MAX_CHARS))
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code >= 400:
            snippet = shrink(self._render(resp, ctype), min(limit, 2000))
            hint = ""
            if resp.status_code in (401, 403):
                hint = self._auth_hint()
            elif resp.status_code == 404:
                hint = " The resource was not found; check the identifiers you passed."
            elif resp.status_code == 429:
                retry = resp.headers.get("retry-after")
                hint = f" Rate limited by the API{f'; retry after {retry}s' if retry else ''}."
            elif resp.status_code in (400, 422):
                hint = " The API rejected the parameters; check names, types and required fields."
            raise ToolError(f"{label} returned HTTP {resp.status_code} {resp.reason_phrase}.{hint}\n{snippet}".strip())
        if resp.status_code == 204 or not resp.content:
            return f"OK (HTTP {resp.status_code}, no content)"
        return shrink(self._render(resp, ctype), limit)

    @staticmethod
    def _render(resp: httpx.Response, ctype: str) -> str:
        if "json" in ctype:
            try:
                return json.dumps(resp.json(), separators=(",", ":"), ensure_ascii=False)
            except ValueError:
                pass
        if ctype.startswith(("text/", "application/xml")) or "xml" in ctype or not ctype:
            return resp.text
        return f"[binary response: {ctype or 'unknown type'}, {len(resp.content)} bytes]"
