# openapi2mcp

Turn any public OpenAPI spec into a working, tested MCP server in minutes. Point it at an API spec and get a server any MCP-compatible client can use, for fast prototyping and evaluation.

- One MCP tool per selected API operation, with typed parameters taken from the spec.
- Tool descriptions built from the spec's summary and description.
- Auth from environment variables (bearer token or API key), never from tool arguments.
- Request timeouts, readable error messages, and response truncation so one big payload cannot flood a model's context.
- Safe by default: only read-only `GET` operations are exposed unless you opt in to more.
- Two ways to run: generate a standalone project, or `serve` a spec directly with no files.

Built on the official MCP Python SDK (`mcp` 2.x, `MCPServer`) and `httpx`. Python 3.12, managed with [uv](https://docs.astral.sh/uv/).

## Quickstart

```bash
git clone <this repo> && cd openapi2mcp
uv sync

# 1. See what a spec would give you (no files written)
uv run openapi2mcp list https://petstore3.swagger.io/api/v3/openapi.json

# 2. Fastest demo: serve it directly over stdio
uv run openapi2mcp serve https://petstore3.swagger.io/api/v3/openapi.json --auth none

# 3. Or generate a standalone project you can edit and hand over
uv run openapi2mcp generate https://petstore3.swagger.io/api/v3/openapi.json \
    --out ./petstore-mcp --auth none
cd petstore-mcp && cp .env.example .env && uv run server.py

# 4. Prove a generated server works: starts it over stdio, lists tools, optionally calls one
uv run openapi2mcp verify ./petstore-mcp --use-uv --call getInventory
```

The spec can be a URL or a local file, JSON or YAML, OpenAPI 3.x or Swagger 2.0.

### Commands

| Command | What it does |
|---|---|
| `generate <spec> --out DIR` | Writes `server.py`, `mcp_runtime.py`, `pyproject.toml`, `README.md`, `.env.example`, `.gitignore` |
| `serve <spec>` | Generates into a temp folder and runs the server (`--transport stdio` default, or `streamable-http` with `--host/--port`) |
| `list <spec>` | Prints the operations that would become tools |
| `verify <dir>` | Launches a generated server over stdio with the MCP client, lists tools, optionally `--call TOOL --args '{...}'` |

### Choosing which operations become tools

These flags work on `generate`, `serve` and `list`:

| Flag | Meaning |
|---|---|
| `--methods GET POST ...` | HTTP methods to expose. **Default: GET only.** Add write methods deliberately. |
| `--include-tags a b` / `--exclude-tags x` | Filter by OpenAPI tag (case-insensitive) |
| `--include-paths '/pet/*'` / `--exclude-paths '/admin/*'` | Filter by path using glob patterns |
| `--include-deprecated` | Deprecated operations are skipped unless you ask |
| `--max-tools N` | Cap tool count (default 40, `0` for no cap). Keeping the list short helps models choose well. |
| `--auth bearer\|apikey\|none` | Credential style. Default `auto` follows the spec's security scheme. |
| `--base-url URL` | Override the server URL from the spec |
| `--name NAME` | Server name |

### Configuration of a generated server

Set in the environment or a `.env` file next to `server.py` (see the generated `.env.example`):

| Variable | Purpose |
|---|---|
| `API_BASE_URL` | Base URL (defaults to the spec's `servers[0]`) |
| `API_TOKEN` | Bearer token (`--auth bearer`) |
| `API_KEY`, `API_KEY_NAME`, `API_KEY_IN` | API key and where to send it (`--auth apikey`) |
| `API_TIMEOUT`, `API_CONNECT_TIMEOUT` | Seconds, default 30 and 10 |
| `MAX_RESPONSE_CHARS` | Truncation limit, default 20000 |
| `API_USER_AGENT`, `API_EXTRA_HEADERS` | Custom User-Agent; extra headers as a JSON object |
| `LOG_LEVEL` | Logs go to stderr; stdout is reserved for the MCP protocol |
| `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT` | `stdio` (default) or `streamable-http` |

## Connect a generated server to a client

Use any MCP-compatible client, for example Cursor or VS Code. Most clients take a JSON entry that says how to launch the server:

```json
{
  "mcpServers": {
    "petstore": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/petstore-mcp", "server.py"],
      "env": { "API_TOKEN": "only-if-the-api-needs-one" }
    }
  }
}
```

The config file location and key names vary by client, so check your client's MCP documentation. Restart or reload the client, then ask it something that needs the API ("what is in the pet store inventory?").

For a remote or shared demo, run the server with `MCP_TRANSPORT=streamable-http` and point the client at the URL.

## Quick walkthrough

1. `openapi2mcp list <spec>` to preview operations, base URL and tags.
2. `openapi2mcp generate <spec> --out ./demo --include-tags ...` (GET-only by default).
3. Put a sandbox or read-only key in `.env`.
4. `openapi2mcp verify ./demo --use-uv --call <tool>`.
5. Tighten tool descriptions in `server.py`; models pick tools by description.

## Production hardening checklist (beyond this kit)

This kit is built for fast prototypes. It is deliberately not a production gateway. Running a generated server for real users usually needs:

- **Authentication.** Per-user credentials and OAuth flows instead of one shared token, secret storage in a vault, scoped keys, token refresh, and a review of which operations should ever be reachable by an AI client.
- **Rate limits and retries.** Client-side throttling that respects the API's quotas, `429` and `Retry-After` handling with backoff, retries for idempotent calls only, circuit breaking, and per-user or per-tenant budgets.
- **Pagination.** Cursor, offset and link-header pagination exposed as tool-friendly "next page" behaviour with sensible page sizes, rather than relying on truncation notices.
- **Logging and observability.** Structured logs with request IDs, secret redaction, metrics, tracing, audit trails of every tool call, and alerting.
- **Write safety.** Confirmation steps, dry-run modes, idempotency keys and allow-lists before enabling `POST`, `PUT`, `PATCH` or `DELETE`.
- **Better tools.** Hand-written tools that combine several API calls, flatten awkward payloads, trim responses to the fields that matter, and carry domain-specific descriptions and examples.
- **Spec gaps.** Request bodies in form or multipart encoding, file uploads and downloads, cookie auth, webhooks, and specs that are incomplete or wrong.
- **Deployment.** Container image, hosted streamable-HTTP transport with TLS and access control, CI, a regression suite against the live API, and a monitoring runbook.

## Known limits of the kit

- Request bodies are accepted as a single `body` JSON argument; their fields are listed in the argument description but not expanded into separate typed parameters. Only JSON bodies are supported (not form, multipart or octet-stream).
- Cookie parameters, webhooks and callbacks are ignored. Only local `$ref`s are resolved.
- Auth supports bearer tokens and one API key (header or query). Basic auth, OAuth flows and multiple simultaneous schemes are not supported yet.
- Responses are returned as text. Long JSON arrays are cut to fit with a notice; there is no automatic pagination.
- Each call opens a new HTTP connection and there is no retry or rate limiting.
- Tool names come from `operationId` (or are derived from method and path). Spec quality drives tool quality.

## Development

```bash
uv sync
uv run pytest        # 45 tests; HTTP calls are mocked with respx
```

Layout: `src/openapi2mcp/spec.py` (load and normalise), `select.py` (filters), `codegen.py` (project rendering), `templates/mcp_runtime.py` (runtime copied into every generated project), `cli.py`, `verify.py`. Worked examples with recorded results are in [`examples/`](examples/README.md).

## Licence

MIT. Copyright (c) 2026 Ankur Kapuriya. See [LICENSE](LICENSE).
