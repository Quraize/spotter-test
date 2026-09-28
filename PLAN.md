# Spotter Fuel Router — Project Plan

Backend Django assessment: given a start and finish location in the USA, return the
route, the cost-optimal fuel stops along it, and the total fuel spend.

## Constraints from the assignment

| Constraint | Value |
|---|---|
| Vehicle range | 500 miles |
| Fuel economy | 10 mpg (50 gallon effective tank) |
| Fuel prices | `data/fuel-prices-for-be-assessment.csv` |
| Routing/map API | free, chosen by us; 1 call per request ideal, 2–3 acceptable |
| Framework | latest stable Django (6.1.1) |
| Deliverables | GitHub repo, Loom (≤ 5 min) with Postman demo + code overview |

## Key findings from the data

- 8,151 rows, 6,738 unique station IDs. 597 IDs repeat with different prices → dedupe, keep cheapest.
- Only 4,275 unique city/state pairs. Addresses are interstate exits ("I-44, EXIT 283") → geocode by city + state.
- ~620 rows are Canadian provinces (AB, BC, ON, QC, …) → drop, assignment is USA only.
- No coordinates in the file → geocode **once, offline**, commit the result.
- Prices range 2.69–6.40, mean 3.50.

## Agreed design decisions

1. **Two separate geocoding jobs.**
   - Stations: offline management command, output committed. Runtime never geocodes stations.
   - Start/finish input: runtime. `lat,lng` → direct. `City, ST` → local US cities table (0 calls).
     Anything else → live geocoder (1 call per endpoint). New addresses fully supported.
2. **Routing provider:** OpenRouteService primary (free key, truck profile, geometry + distance in one call).
   OSRM public demo as fallback. Both behind one `RouteProvider` interface.
3. **Geocoding provider:** ORS geocode primary (same key/quota pool), Nominatim fallback.
4. **Ambiguous inputs** (e.g. "Springfield" with no state): skip local table, let live geocoder rank,
   echo the resolved place back in the response. Never block with a 400.
5. **Initial fuel:** default **empty tank**. Entire trip fuel is purchased en route; first stop is the
   first station encountered. `initial_fuel_miles` query param overrides (e.g. 500 = full tank).
6. **Algorithm:** greedy fill-up (Khuller, Malekian, Mestre — "To fill or not to fill").
   At each stop: if a cheaper station is reachable within range, buy just enough to reach it;
   otherwise fill up and go to the cheapest reachable station. Linear time, provably optimal for
   fractional purchases.
7. **Station lookup:** in-memory numpy index loaded once per process; bounding-box prefilter, then
   shapely projection onto the route polyline for mile marker + detour. Corridor tolerance ~5 miles.
8. **"Map":** GeoJSON route + stops in the JSON response, plus an HTML Leaflet page rendering it.
   Tiles are not routing calls.
9. **Stack:** Django 6.1.1, DRF, drf-spectacular, django-environ, httpx, shapely, numpy, SQLite,
   uv, ruff, pytest-django, Docker/gunicorn.
10. **External call budget per request:** 1 (coords or known city) / 3 (free-text addresses).
    Routing responses cached by rounded coordinates.

## Project structure

```
spotter-fuel-router/
├── pyproject.toml            # uv-managed, pinned deps, ruff + pytest config
├── uv.lock
├── .env.example              # ORS_API_KEY, DJANGO_SECRET_KEY, DEBUG, ROUTING_PROVIDER
├── Dockerfile / compose.yaml # gunicorn, single service, SQLite volume
├── Makefile                  # setup, import, geocode, test, run
├── README.md                 # setup, assumptions, call budget, API contract
├── manage.py
├── config/
│   ├── settings.py           # env-driven via django-environ, one file
│   ├── urls.py
│   └── wsgi.py / asgi.py
├── data/
│   ├── fuel-prices-for-be-assessment.csv   # original, untouched
│   ├── us_cities.csv                       # local geocoding table
│   └── stations_geocoded.csv               # committed output of the geocode command
├── apps/
│   ├── stations/             # reference data
│   │   ├── models.py         # Station: opis_id, name, address, city, state, price, lat, lng
│   │   ├── index.py          # StationIndex: numpy arrays loaded once, bbox prefilter
│   │   └── management/commands/
│   │       ├── import_stations.py   # CSV -> DB: dedupe by ID, drop non-US, keep cheapest
│   │       └── geocode_stations.py  # city/state -> local table -> Nominatim fallback, resumable
│   ├── routing/              # all external I/O lives here
│   │   ├── base.py           # RouteProvider / GeocodeProvider protocols, Route + Place dataclasses
│   │   ├── ors.py            # OpenRouteService directions + geocode
│   │   ├── osrm.py           # fallback routing
│   │   ├── nominatim.py      # fallback geocoding
│   │   └── resolver.py       # input -> latlng | local city hit | live geocode
│   ├── planner/              # pure domain, zero Django imports
│   │   ├── types.py          # Candidate, FuelStop, FuelPlan dataclasses
│   │   ├── corridor.py       # shapely projection: mile marker + detour per station
│   │   └── optimizer.py      # greedy fill-up algorithm
│   └── api/
│       ├── serializers.py    # request validation, response shape
│       ├── views.py          # RoutePlanView: resolver -> route -> corridor -> optimizer
│       ├── urls.py
│       ├── throttles.py
│       ├── exceptions.py     # upstream failures -> clean 502/504 with detail
│       └── templates/api/map.html   # Leaflet page fed by the plan JSON
└── tests/
    ├── test_optimizer.py     # the important one
    ├── test_corridor.py
    ├── test_resolver.py
    ├── test_import.py
    └── test_api.py           # providers mocked, full request cycle
```

## API contract

```
GET /api/v1/route-plan/?start=Chicago, IL&finish=Dallas, TX&initial_fuel_miles=0
```

```json
{
  "start":  {"query": "Chicago, IL", "name": "Chicago, IL, USA", "lat": 41.88, "lng": -87.63, "source": "local"},
  "finish": {"query": "Dallas, TX",  "name": "Dallas, TX, USA",  "lat": 32.78, "lng": -96.80, "source": "local"},
  "route":  {"distance_miles": 925.4, "duration_minutes": 811, "geometry": {"type": "LineString", "coordinates": []}},
  "fuel_stops": [
    {
      "station": {"opis_id": 123, "name": "...", "address": "...", "city": "...", "state": "IL", "lat": 0, "lng": 0},
      "mile_marker": 3.1,
      "detour_miles": 0.4,
      "price_per_gallon": 3.09,
      "gallons": 21.7,
      "cost": 67.05,
      "fuel_after_miles": 220
    }
  ],
  "summary": {"total_gallons": 92.54, "total_fuel_cost": 301.22, "stops": 3, "mpg": 10, "range_miles": 500},
  "assumptions": {"initial_fuel_miles": 0, "corridor_miles": 5},
  "map_url": "/api/v1/route-plan/map/?start=Chicago%2C+IL&finish=Dallas%2C+TX",
  "external_calls": 1
}
```

Errors: 400 for invalid input, 422 if a location cannot be resolved or lies outside the USA,
502/504 for upstream routing/geocoding failures, with a `detail` message in every case.

## Build order

### Phase 0 — Scaffold (~30 min)
- [ ] `uv init`, Django 6.1.1, DRF, drf-spectacular, django-environ, httpx, shapely, numpy
- [ ] ruff, pytest-django, `.env.example`, `.gitignore`, `Makefile`
- [ ] `config/settings.py` env-driven; `/api/v1/health/` endpoint
- [ ] First commit

### Phase 1 — Station data pipeline (~2 h, then geocode runs in background)
- [ ] `Station` model + migration
- [ ] `import_stations`: parse CSV, drop non-US, dedupe by OPIS ID keeping cheapest, bulk insert
- [ ] Download `us_cities.csv` (free, attributed) into `data/`
- [ ] `geocode_stations`: local city/state join → Nominatim fallback (1 req/s, proper User-Agent),
      resumable, writes `data/stations_geocoded.csv`
- [ ] Start the geocode run early; commit output when done
- [ ] Tests: import dedupe/filter logic

### Phase 2 — Planner domain, test first (~2 h)
- [ ] `types.py` dataclasses
- [ ] `optimizer.py` greedy fill-up
- [ ] Tests: no stops needed, single stop, cheaper station ahead within range, no cheaper ahead,
      unreachable gap (> 500 mi between stations → explicit error), initial fuel variants,
      finish reached with minimal leftover fuel
- [ ] `corridor.py`: bbox prefilter + shapely `project()` → mile marker, `distance()` → detour
- [ ] Tests on a synthetic route

### Phase 3 — Routing clients (~1.5 h)
- [ ] `base.py` protocols + `Route`/`Place` dataclasses
- [ ] `ors.py` directions (driving-hgv) + geocode; `osrm.py` fallback
- [ ] httpx client with hard timeouts, one retry, normalized output (miles, minutes, GeoJSON)
- [ ] Route cache keyed by rounded coordinates (Django cache framework)
- [ ] Tests with recorded fixtures, no live calls

### Phase 4 — Input resolver (~1 h)
- [ ] `lat,lng` parsing + USA bounds check
- [ ] Local city table lookup with state disambiguation; ambiguous → live geocoder
- [ ] Live geocode via provider interface, resolved name echoed back
- [ ] Tests

### Phase 5 — API layer (~1.5 h)
- [ ] Request serializer (start, finish, `initial_fuel_miles`, optional `corridor_miles`)
- [ ] `RoutePlanView` orchestration, response serializer
- [ ] Throttling, upstream error mapping, OpenAPI schema at `/api/schema/` + Swagger UI
- [ ] End-to-end tests with mocked providers

### Phase 6 — Map page (~45 min)
- [ ] `map.html` with Leaflet: route line, numbered stop markers, price/gallons popups, summary box
- [ ] `/api/v1/route-plan/map/` view reusing the same plan (served from cache)

### Phase 7 — Hardening (~1.5 h)
- [ ] Dockerfile + compose, gunicorn, logging config
- [ ] README: setup, assumptions, algorithm, call budget, provider limits, attribution
- [ ] Postman collection committed under `postman/`
- [ ] Full test run, ruff clean, final review pass

### Phase 8 — Loom prep (~30 min)
- [ ] Two or three prepared requests (short trip, cross-country, free-text address)
- [ ] Script: demo → algorithm → call budget → code tour, under 5 minutes

**Estimated total:** ~12 h focused work. Day 1 build, day 2 polish + recording.
