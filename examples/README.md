# Worked examples

Two real public APIs, no credentials needed. Both were generated and then started over stdio and queried with the MCP Python SDK client (run on 2026-11-17, Python 3.12, `mcp` 2.3.0). Live data on public demo APIs changes, so your payloads will differ.

## 1. Swagger Petstore v3 (`examples/petstore/`)

Generate (default selection: read-only GET operations, deprecated skipped):

```bash
uv run openapi2mcp generate https://petstore3.swagger.io/api/v3/openapi.json \
  --out examples/petstore --auth none
```

Result: 19 operations in the spec, 8 selected as tools (the other 11 are POST/PUT/DELETE, left out by the read-only default).

Start the generated project with `uv run` (this resolves its own dependencies, which proves it is standalone), list its tools over stdio and call one:

```bash
uv run openapi2mcp verify examples/petstore --use-uv --call getInventory
```

```
server: swagger-petstore-openapi-3-0
tools: 8
  - findPetsByStatus(status): Finds Pets by status.
  - findPetsByTags(tags): Finds Pets by tags.
  - getPetById(petId): Find pet by ID.
  - getInventory(): Returns pet inventories by status.
  - getOrderById(orderId): Find purchase order by ID.
  - loginUser(password, username): Logs user into the system.
  - logoutUser(): Logs out current logged in user session.
  - getUserByName(username): Get user by user name.
call getInventory: is_error=False length=42
  {"approved":50,"placed":34,"delivered":52}
```

Note: the public Petstore demo server is shared and flaky. During this run `getPetById` for id 1 returned an HTTP 500 from the upstream, and the tool surfaced it as a readable error (`GET /pet/{petId} returned HTTP 500 Internal Server Error. ...`) instead of crashing, which is the intended behaviour.

## 2. US National Weather Service API (`examples/weather-gov/`)

A large, real-world spec (69 operations, OpenAPI 3.1, no auth, but it asks clients to send a User-Agent). Generated with a curated path filter so the tool list stays useful for a model:

```bash
uv run openapi2mcp generate https://api.weather.gov/openapi.json \
  --out examples/weather-gov --auth none \
  --include-paths /alerts/active '/alerts/active/area/*' /alerts/types '/points/*' \
      '/gridpoints/*/forecast' '/gridpoints/*/forecast/hourly' \
      '/stations/*/observations/latest' '/offices/*' \
  --exclude-paths '/offices/*/briefing*' '/offices/*/weatherstories*'
```

Result: 69 operations in the spec, 11 selected. Verify and call `point` (resolves a lat/long to a forecast grid):

```bash
uv run openapi2mcp verify examples/weather-gov --use-uv \
  --call point --args '{"latitude":"39.7456","longitude":"-97.0892"}'
```

```
server: weather-gov-api
tools: 11
  - alerts_active(area, certainty, code, event, message_type, point, region, region_type, severity, status, urgency, zone): Returns all currently active alerts
  - alerts_active_area(area): Returns active alerts for the given area (state or marine area)
  - alerts_types(): Returns a list of alert types
  - gridpoint_forecast(Feature_Flags, units, wfo, x, y): Returns a textual forecast for a 2.5km grid area
  - gridpoint_forecast_hourly(Feature_Flags, units, wfo, x, y): Returns a textual hourly forecast for a 2.5km grid area
  - station_observation_latest(require_qc, stationId): Returns the latest observation for a station
  - office(officeId): Returns metadata about a NWS forecast office
  - office_headline(headlineId, officeId): Returns a specific news headline for a given NWS office
  - office_headlines(officeId): Returns a list of news headlines for a given NWS office
  - point(latitude, longitude): Returns metadata about a given latitude/longitude point
  - point_radio(latitude, longitude): Returns NOAA Weather Radio broadcast script for a latitude/longitude point
call point: is_error=False length=2463
  {"@context":["https://geojson.org/geojson-ld/geojson-context.jsonld",...
```

Other calls that also succeeded in the same session: `gridpoint_forecast` with `{"wfo":"TOP","x":31,"y":80}` (7,724 characters of forecast JSON), `alerts_active_area` with `{"area":"TX"}` (18,477 characters, just under the 20,000 default cap), and `office` with a bad id, which returned `is_error=True` with `HTTP 404 Not Found`, a hint to check identifiers, and the API's own validation message.

## 3. Serve mode (no files kept)

```bash
uv run python examples/verify_serve.py https://petstore3.swagger.io/api/v3/openapi.json --auth none
```

```
8 tools from serve mode:
  - findPetsByStatus
  - findPetsByTags
  - getPetById
  - getInventory
  - getOrderById
  - loginUser
  - logoutUser
  - getUserByName
```

`openapi2mcp serve` generates into a temporary folder, runs the server and cleans up when it exits.

## Running the checks yourself

```bash
uv run pytest            # 45 tests, no network needed
uv run openapi2mcp verify examples/petstore --use-uv --call getInventory
```

The `.venv` and `uv.lock` files that `uv run` creates inside each example are git-ignored or safe to delete.
