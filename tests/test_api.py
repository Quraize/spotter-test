"""
API tests: full request cycle with the network stubbed out.

The station catalog, resolver and route provider are replaced with in-memory fakes built
around one synthetic route (a straight line east along latitude 40, ~530 miles), so the
real corridor search, optimiser, serializers and error mapping all run.
"""

from __future__ import annotations

import numpy as np
import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api import services
from apps.planner.geo import MILES_PER_DEG_LAT
from apps.routing.base import Place, ProviderError, ProviderTimeout, Route
from apps.routing.http import ProviderClientError
from apps.routing.resolver import LocationNotFound
from apps.stations.index import StationCatalog, StationDataMissing, StationInfo

LAT = 40.0
MILES_PER_DEG_LNG = MILES_PER_DEG_LAT * np.cos(np.radians(LAT))
START = Place("Start, IL, USA", LAT, -90.0, "local")
FINISH = Place("Finish, OH, USA", LAT, -80.0, "local")


def synthetic_route() -> Route:
    lngs = np.linspace(-90.0, -80.0, 201)
    coords = [(float(x), LAT) for x in lngs]
    return Route(
        distance_miles=float(10 * MILES_PER_DEG_LNG),
        duration_minutes=600.0,
        coordinates=coords,
        provider="stub",
    )


def station(opis_id: int, lng: float, price: float, lat: float = LAT) -> StationInfo:
    return StationInfo(
        opis_id=opis_id,
        name=f"STATION {opis_id}",
        address=f"I-70, EXIT {opis_id}",
        city=f"Town{opis_id}",
        state="OH",
        price=price,
        lat=lat,
        lng=lng,
    )


class StubResolver:
    def __init__(self) -> None:
        self.places = {"Start, IL": START, "Finish, OH": FINISH}
        self.fail: Exception | None = None

    def resolve(self, raw: str) -> Place:
        if self.fail:
            raise self.fail
        try:
            return self.places[raw]
        except KeyError:
            raise LocationNotFound(raw, "no match in stub") from None


class StubRouter:
    name = "stub"

    def __init__(self, route: Route) -> None:
        self.route_result = route
        self.fail: Exception | None = None
        self.calls = 0

    def route(self, start: Place, finish: Place) -> Route:
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.route_result


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch):
    resolver = StubResolver()
    router = StubRouter(synthetic_route())
    # Stations at ~0, ~132, ~265, ~397 and ~530 miles; one 20 miles off-route; one far away.
    catalog = StationCatalog(
        [
            station(1, -89.98, 3.50),
            station(2, -87.5, 3.00),
            station(3, -85.0, 3.40),
            station(4, -82.5, 2.80),
            station(5, -80.02, 3.90),
            station(6, -85.0, 1.00, lat=LAT + 20 / MILES_PER_DEG_LAT),  # outside corridor
            station(7, -100.0, 1.00),  # nowhere near
        ]
    )
    monkeypatch.setattr(services, "get_resolver", lambda: resolver)
    monkeypatch.setattr(services, "get_route_provider", lambda: router)
    monkeypatch.setattr(services, "get_station_catalog", lambda: catalog)
    cache.clear()
    return resolver, router, catalog


@pytest.fixture
def client() -> APIClient:
    return APIClient()


URL = reverse("api:route-plan")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_health_returns_ok(client: APIClient) -> None:
    response = client.get(reverse("api:health"))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["django"].startswith("6.1")


def test_openapi_schema_is_served(client: APIClient) -> None:
    response = client.get(reverse("schema"))

    assert response.status_code == 200
    assert b"route-plan" in response.content


def test_plan_via_get(client: APIClient, stubs) -> None:
    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 200, response.content
    body = response.json()

    assert body["start"] == {
        "query": "Start, IL",
        "name": "Start, IL, USA",
        "lat": 40.0,
        "lng": -90.0,
        "source": "local",
    }
    assert body["route"]["provider"] == "stub"
    assert body["route"]["distance_miles"] == pytest.approx(10 * MILES_PER_DEG_LNG, abs=0.1)
    assert body["route"]["geometry"]["type"] == "LineString"
    coords = body["route"]["geometry"]["coordinates"]
    assert coords[0] == [-90.0, 40.0] and coords[-1] == [-80.0, 40.0]
    assert len(coords) == 2  # a straight line simplifies to its endpoints for transport
    assert body["vehicle"] == {"range_miles": 500.0, "mpg": 10.0, "tank_gallons": 50.0}

    # Empty tank: 1.3 miles to station 1 are a pre-trip purchase; then buy just enough at
    # station 1 ($3.50) to reach station 2 ($3.00) and fill the rest there. Stations 3/4 are
    # skipped because the $10 stop penalty outweighs their savings.
    stops = body["fuel_stops"]
    assert [s["station"]["opis_id"] for s in stops] == [1, 2]
    assert [s["order"] for s in stops] == [1, 2]
    assert stops[0]["station"]["name"] == "STATION 1"
    assert stops[0]["price_per_gallon"] == 3.5
    assert stops[0]["fuel_on_arrival_gallons"] == 0.0
    assert stops[0]["detour_miles"] < 0.1
    assert stops[1]["fuel_on_arrival_gallons"] == 0.0
    assert all(s["gallons"] > 0 for s in stops)

    pre = body["pre_trip_purchase"]
    assert pre["station"]["opis_id"] == 1
    assert pre["gallons"] == pytest.approx(stops[0]["mile_marker"] / 10, abs=0.05)
    assert "before departure" in pre["note"]

    summary = body["summary"]
    assert summary["stop_count"] == 2
    assert summary["candidate_stations"] == 5
    assert summary["total_gallons_purchased"] == pytest.approx(
        summary["gallons_consumed"], abs=0.01
    )
    assert summary["total_fuel_cost"] == pytest.approx(
        sum(s["cost"] for s in stops) + pre["cost"], abs=0.02
    )
    assert body["assumptions"]["initial_fuel_miles"] == 0.0
    assert body["assumptions"]["stop_penalty_usd"] == 10.0
    assert body["assumptions"]["corridor_miles"] == 10.0
    assert body["external_calls"] == {"count": 0, "providers": []}
    assert set(body["timings_ms"]) == {"resolve", "route", "corridor", "optimize"}


def test_plan_via_post_matches_get(client: APIClient, stubs) -> None:
    get = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"}).json()
    post = client.post(URL, {"start": "Start, IL", "finish": "Finish, OH"}, format="json").json()

    get.pop("timings_ms"), post.pop("timings_ms")
    assert get == post


def test_full_tank_needs_one_small_top_up(client: APIClient, stubs) -> None:
    response = client.get(
        URL, {"start": "Start, IL", "finish": "Finish, OH", "initial_fuel_miles": 500}
    )

    assert response.status_code == 200
    body = response.json()
    # ~530 mile trip on a 500 mile tank: buy the missing ~30 miles at the cheapest reachable
    # station (station 4, $2.80). No pre-trip purchase because the tank was full.
    assert [s["station"]["opis_id"] for s in body["fuel_stops"]] == [4]
    assert body["pre_trip_purchase"] is None
    shortfall_gallons = (10 * MILES_PER_DEG_LNG - 500) / 10
    assert body["fuel_stops"][0]["gallons"] == pytest.approx(shortfall_gallons, abs=0.05)
    assert body["summary"]["total_fuel_cost"] == pytest.approx(shortfall_gallons * 2.8, abs=0.05)
    assert body["summary"]["fuel_at_finish_gallons"] == 0.0


def test_zero_penalty_and_corridor_override(client: APIClient, stubs) -> None:
    response = client.get(
        URL,
        {"start": "Start, IL", "finish": "Finish, OH", "stop_penalty": 0, "corridor_miles": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["candidate_stations"] == 6  # the 20-mile-off station now counts
    assert body["assumptions"]["stop_penalty_usd"] == 0.0
    ids = [s["station"]["opis_id"] for s in body["fuel_stops"]]
    assert ids[0] == 1  # no penalty: chase every cheaper station, starting at the first
    assert 6 in ids  # the $1.00 station 20 miles off-route is now worth the detour


def test_include_geometry_false_omits_linestring(client: APIClient, stubs) -> None:
    response = client.get(
        URL, {"start": "Start, IL", "finish": "Finish, OH", "include_geometry": "false"}
    )

    assert response.status_code == 200
    assert response.json()["route"]["geometry"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_fields_are_400(client: APIClient, stubs) -> None:
    response = client.get(URL, {"start": "Start, IL"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid"
    assert "finish" in body["errors"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_fuel_miles", -1),
        ("initial_fuel_miles", 501),
        ("stop_penalty", -5),
        ("corridor_miles", 0.5),
        ("corridor_miles", 100),
        ("start", "x" * 201),
    ],
)
def test_out_of_range_parameters_are_400(client: APIClient, stubs, field, value) -> None:
    params = {"start": "Start, IL", "finish": "Finish, OH", field: value}

    response = client.get(URL, params)

    assert response.status_code == 400
    assert field in response.json()["errors"]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_unresolvable_location_is_422(client: APIClient, stubs) -> None:
    response = client.get(URL, {"start": "Atlantis", "finish": "Finish, OH"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unresolvable_location"
    assert "Atlantis" in body["detail"]


def test_station_gap_is_422_planning_error(client: APIClient, stubs, monkeypatch) -> None:
    _, _, catalog = stubs
    sparse = StationCatalog([station(1, -89.98, 3.5), station(5, -80.02, 3.9)])  # ~530 mi apart
    monkeypatch.setattr(services, "get_station_catalog", lambda: sparse)

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "planning_error"
    assert "exceeds the vehicle range" in body["detail"]


def test_no_stations_in_corridor_is_422(client: APIClient, stubs, monkeypatch) -> None:
    monkeypatch.setattr(
        services, "get_station_catalog", lambda: StationCatalog([station(7, -100.0, 1.0)])
    )

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 422
    assert "No fuel stations found within 10 miles" in response.json()["detail"]


def test_provider_timeout_is_504(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    router.fail = ProviderTimeout("ors: timed out")

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 504
    assert response.json()["code"] == "upstream_timeout"


def test_provider_outage_is_502(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    router.fail = ProviderError("ors: upstream HTTP 500")

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 502
    assert response.json()["code"] == "upstream_error"


def test_provider_bad_input_is_422_unroutable(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    router.fail = ProviderClientError("ors", 404, "Could not find routable point")

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 422
    assert response.json()["code"] == "unroutable"


def test_missing_station_data_is_503(client: APIClient, stubs, monkeypatch) -> None:
    def boom():
        raise StationDataMissing("no geocoded stations loaded")

    monkeypatch.setattr(services, "get_station_catalog", boom)

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 503
    assert response.json()["code"] == "service_not_ready"


def test_unexpected_error_is_500_with_contract(client: APIClient, stubs) -> None:
    _, router, _ = stubs
    router.fail = RuntimeError("kaboom")

    response = client.get(URL, {"start": "Start, IL", "finish": "Finish, OH"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error.", "code": "server_error"}


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


def test_throttle_kicks_in(client: APIClient, stubs, monkeypatch) -> None:
    from rest_framework.throttling import AnonRateThrottle

    # DRF copies DEFAULT_THROTTLE_RATES onto the class at import time, so patch that.
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", {"anon": "2/min"})
    params = {"start": "Start, IL", "finish": "Finish, OH"}

    assert client.get(URL, params).status_code == 200
    assert client.get(URL, params).status_code == 200
    response = client.get(URL, params)

    assert response.status_code == 429
    assert response.json()["code"] == "throttled"
    assert "Retry-After" in response.headers
