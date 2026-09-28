# Spotter Fuel Router

A Django REST API that plans a road trip between two points in the USA and picks the most
cost-effective places to buy fuel along the way, for a vehicle with a **500 mile range** and
**10 mpg**, using the supplied OPIS truck-stop price list.

- **One request → one upstream routing call.** Start/finish given as `City, ST` or `lat,lng`
  resolve locally; routes are cached, so repeats make no upstream calls at all. Free-text
  addresses cost one geocoding call each. The response reports `external_calls`.
- **Provably cost-optimal stops.** An exact dynamic program over the stations in the route
  corridor, checked in tests against a brute-force oracle on 1,500+ random instances.
- **Fast.** All work after the routing call takes under 60 ms; a cached route answers in ~25 ms.
- **A map.** Every response links to an HTML page that draws the route and stops with Leaflet.

```
GET /api/v1/route-plan/?start=Chicago, IL&finish=Dallas, TX
```

| Route | Cold | Cached | Upstream calls | Plan |
|---|---|---|---|---|
| Chicago → Dallas (975 mi) | ~0.5 s | 26 ms | 1 → 0 | 3 stops, $296.97 |
| Los Angeles → New York (2,796 mi) | ~2 s | 116 ms | 1 → 0 | 6 stops, $877.20 |

Interactive docs: `/api/docs/` (Swagger UI) · schema: `/api/schema/` · map: `/api/v1/route-plan/map/`

---

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). A free
[OpenRouteService](https://openrouteservice.org/dev/#/signup) key is recommended (without one
the app falls back to the public OSRM demo server for routing and Nominatim for geocoding).

```bash
git clone https://github.com/Quraize/spotter-test.git && cd spotter-test
uv sync                       # creates .venv with pinned dependencies
cp .env.example .env          # then set ORS_API_KEY and a real contact in HTTP_USER_AGENT
uv run python manage.py migrate
uv run python manage.py import_stations   # ~2 s, no network: coordinates are committed
uv run python manage.py runserver
```

```bash
curl "http://127.0.0.1:8000/api/v1/route-plan/?start=Chicago,%20IL&finish=Dallas,%20TX&include_geometry=false"
```

`make setup migrate import run` does the same. `make check` runs ruff and the test suite.

### Docker

```bash
cp .env.example .env          # set ORS_API_KEY, DJANGO_SECRET_KEY, ALLOWED_HOSTS
docker compose up -d --build  # http://127.0.0.1:8081
```

The container migrates and imports stations on start, runs gunicorn as a non-root user, and
has a health check on `/api/v1/health/`. `SPOTTER_PORT` and `SPOTTER_HOST_BIND` in `.env`
control the host binding (defaults `8081` on `127.0.0.1`, for use behind a reverse proxy).

A ready-made Postman collection is in [`postman/`](postman/).

---

## API

### `GET|POST /api/v1/route-plan/`

Same fields as query parameters (GET) or a JSON body (POST).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `start`, `finish` | string | required | `City, ST`, `City, State`, a street address, a ZIP, or `lat,lng`. Must be in the USA. |
| `initial_fuel_miles` | 0–500 | `0` | Miles of range in the tank at departure. `0` = empty, `500` = full. |
| `stop_penalty` | ≥ 0 | `10` | Dollar value of a driver's time per stop. `0` = pure cost minimum. |
| `corridor_miles` | 1–25 | `10` | How far off the route a station may be to count as "along the route". |
| `include_geometry` | bool | `true` | Set `false` to omit the route LineString (2 KB instead of ~100 KB). |

Response (abridged):

```json
{
  "start":  {"query": "Chicago, IL", "name": "Chicago, IL, USA", "lat": 41.8858, "lng": -87.6181, "source": "local"},
  "finish": {"query": "Dallas, TX",  "name": "Dallas, TX, USA",  "lat": 32.7935, "lng": -96.7667, "source": "local"},
  "route":  {"provider": "ors", "distance_miles": 975.1, "duration_minutes": 1311, "geometry": {"type": "LineString", "coordinates": [[-87.62977, 41.8781], "..."]}},
  "vehicle": {"range_miles": 500.0, "mpg": 10.0, "tank_gallons": 50.0},
  "fuel_stops": [
    {"order": 1, "station": {"opis_id": 45101, "name": "Gas N Wash", "address": "I-55 & I-90/94, EXIT 292", "city": "Chicago", "state": "IL", "lat": 41.8858, "lng": -87.6181},
     "mile_marker": 0.0, "detour_miles": 0.0, "price_per_gallon": 3.399, "gallons": 2.07, "cost": 7.03,
     "fuel_on_arrival_gallons": 0.0, "fuel_on_departure_gallons": 2.07},
    {"order": 2, "station": {"name": "QUIKTRIP #7205", "city": "Lansing", "state": "IL", "...": "..."}, "mile_marker": 20.7, "price_per_gallon": 3.079, "gallons": 46.2, "cost": 142.24, "...": "..."},
    {"order": 3, "station": {"name": "SHELL", "city": "Osceola", "state": "AR", "...": "..."}, "mile_marker": 482.6, "price_per_gallon": 2.999, "gallons": 49.25, "cost": 147.70, "...": "..."}
  ],
  "pre_trip_purchase": null,
  "summary": {"total_fuel_cost": 296.97, "total_gallons_purchased": 97.51, "gallons_consumed": 97.51,
              "fuel_at_finish_gallons": 0.0, "stop_count": 3, "candidate_stations": 191},
  "assumptions": {"initial_fuel_miles": 0.0, "stop_penalty_usd": 10.0, "corridor_miles": 10.0,
                  "optimizer": "exact DP minimising fuel cost + stop_penalty per stop", "prices": "OPIS retail diesel prices from the supplied file"},
  "external_calls": {"count": 1, "providers": ["ors"]},
  "timings_ms": {"resolve": 0.0, "route": 473.7, "corridor": 12.9, "optimize": 10.7},
  "map_url": "http://127.0.0.1:8000/api/v1/route-plan/map/?start=Chicago%2C+IL&finish=Dallas%2C+TX&initial_fuel_miles=0&stop_penalty=10&corridor_miles=10"
}
```

Errors always have the shape `{"detail": "...", "code": "..."}`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid` | Missing or out-of-range parameters (`errors` lists the fields). |
| 422 | `unresolvable_location` | A location could not be resolved to a place inside the USA. |
| 422 | `unroutable` | The routing provider rejected the pair (e.g. Honolulu → Dallas). |
| 422 | `planning_error` | A gap between usable stations exceeds the 500 mile range. |
| 429 | `throttled` | Rate limit (60/min per client by default). |
| 502 / 504 | `upstream_error` / `upstream_timeout` | Every routing/geocoding provider failed or timed out. |
| 503 | `service_not_ready` | Station data has not been imported. |

### `GET /api/v1/route-plan/map/`

Same parameters; returns an HTML page (Leaflet) with the route, numbered stop markers with
popups, totals, and a form to try other routes. Rendered server-side from the cached route,
so opening it after an API call makes no upstream request.

### `GET /api/v1/health/`

Liveness probe. Touches nothing external.

---

## How it works

```
input ──► resolver ──► routing provider ──► corridor search ──► optimiser ──► response
          0 calls       1 call (cached)      in-memory index     exact DP
```

1. **Resolve.** `lat,lng` is used directly. `City, ST` (and `City, State`, `City ST`, an
   unambiguous bare city) is looked up in a bundled table of ~30k US cities. Anything else,
   or an ambiguous name like `Springfield`, goes to the live geocoder (ORS, falling back to
   Nominatim). Every result is checked against the real US boundary polygon.
2. **Route.** One call to OpenRouteService on the truck profile (`driving-hgv`), falling back
   to OSRM. Routes are cached for 24 h keyed by endpoints rounded to ~11 m.
3. **Corridor.** All 6,626 geocoded stations live in an in-memory STRtree (spherical
   Mercator, which is conformal so nearest points are geometrically right). Stations within
   `corridor_miles` of the route get a geodesic mile marker and detour distance.
4. **Optimise.** Fuel is tracked in miles of range. The solver minimises
   *fuel cost + stop_penalty × stops* with an exact dynamic program built on the
   Khuller–Malekian–Mestre structure lemma (at each stop you either fill up or buy just
   enough to reach the next stop), so the state space is small and it runs in a few ms.
   With `stop_penalty=0` it is the pure cost optimum and coincides with their greedy, which
   is kept as a reference solver and cross-checked in tests.

### Assumptions, stated

- **Empty tank at departure** by default, so every mile of the trip is paid for. If the
  initial fuel cannot reach the first station on the route, the shortfall is a
  `pre_trip_purchase` priced at that station. Pass `initial_fuel_miles=500` for a full tank.
- **Stop penalty of $10.** The pure cost optimum pulls in at every marginally cheaper station
  for a couple of gallons (18 stops LA → NYC). A penalty representing the driver's time gives
  realistic plans at 1–6 % over the theoretical minimum. The reported total is fuel only.
- **Station positions are city centroids.** The price file has no coordinates and its
  addresses are interstate exits ("I-44, EXIT 283"), so stations were geocoded once, offline,
  by city + state (3,802 from the bundled table, 6 via Nominatim) and the result committed.
  The 10 mile corridor absorbs the error. Canadian rows were dropped; duplicate station IDs
  keep the cheapest price.
- **Detours are reported, not charged.** Stations are within 10 miles of the route by
  construction; the few extra miles are shown per stop but not added to the fuel maths.
- Prices are the file's retail diesel prices, treated as current.

---

## Project layout

```
config/            settings (env-driven), urls, wsgi/asgi (with warm-up)
apps/stations/     Station model, CSV loader, geocoding + import commands, in-memory catalog
apps/routing/      provider clients (ORS, OSRM, Nominatim), fallback chains, caches, resolver
apps/planner/      pure-Python domain: geodesy, corridor search, optimiser (no Django imports)
apps/api/          DRF serializers/views, service orchestration, error contract, map template
data/              price CSV, city table, geocoded places, US boundary (see data/README.md)
docker/            entrypoint and gunicorn config
postman/           Postman collection
tests/             181 tests: unit, oracle-based, provider mocks (respx), full request cycle
```

## Development

```bash
uv run pytest            # 181 tests, ~3 s
uv run ruff check .      # lint
uv run ruff format .     # format
uv run python manage.py geocode_stations   # re-run the offline geocoding (resumable)
```

## Configuration

All settings come from the environment (or `.env`); see `.env.example`. Notable ones:

| Variable | Purpose |
|---|---|
| `ORS_API_KEY`, `ORS_PROFILE` | OpenRouteService key and routing profile (`driving-hgv` default). |
| `ROUTING_PROVIDERS`, `GEOCODING_PROVIDERS` | Ordered fallback chains (`ors,osrm` / `ors,nominatim`). |
| `HTTP_USER_AGENT` | Must contain a real contact; Nominatim rejects placeholders. |
| `CACHE_URL` | Route/geocode cache. Per-process memory by default; use Redis for multiple workers. |
| `API_THROTTLE_RATE` | DRF anonymous throttle, default `60/min`. |
| `MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION` | Basemap for the map page (Esri World Street Map by default, no key). |

Provider limits (free tiers at time of writing): ORS ~2,000 directions and ~1,000 geocodes per
day; OSRM demo server is non-commercial and ~1 request/s; Nominatim 1 request/s.

## Data and attribution

See [`data/README.md`](data/README.md). US cities table: MIT (kelvins/US-Cities-Database).
US boundary: Natural Earth, public domain. Map tiles: Esri, with attribution shown on the map.
Routing and geocoding: © OpenRouteService / OpenStreetMap contributors.
