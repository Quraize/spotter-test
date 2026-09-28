"""
Routing/geocoding provider tests. All HTTP is mocked with respx; nothing here goes online.

Fixtures under tests/fixtures are trimmed live recordings (ORS directions on driving-hgv,
ORS geocode, OSRM route), captured 2026-09-28.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from django.core.cache import cache

from apps.routing.base import METERS_PER_MILE, Place, ProviderError, ProviderTimeout, Route
from apps.routing.calls import record_call, track_calls
from apps.routing.http import ProviderClientError, make_client, request_json
from apps.routing.nominatim import NominatimGeocoder
from apps.routing.ors import ORSClient
from apps.routing.osrm import OSRMRouter
from apps.routing.providers import (
    CachedRouteProvider,
    FallbackGeocodeProvider,
    FallbackRouteProvider,
    get_geocode_provider,
    get_route_provider,
    reset_providers,
)

FIXTURES = Path(__file__).parent / "fixtures"
ORS = "https://ors.test"
OSRM = "https://osrm.test"
NOMINATIM = "https://nominatim.test"

CHICAGO = Place("Chicago, IL, USA", 41.8781, -87.6298, "local")
DALLAS = Place("Dallas, TX, USA", 32.7767, -96.797, "local")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    reset_providers()
    yield
    cache.clear()
    reset_providers()


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


@respx.mock
def test_request_json_retries_once_on_5xx_and_counts_calls() -> None:
    route = respx.get("https://x.test/ping").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    with track_calls() as calls:
        data = request_json(make_client("https://x.test"), "GET", "/ping", provider="x")

    assert data == {"ok": True}
    assert route.call_count == 2
    assert calls == ["x", "x"]


@respx.mock
def test_request_json_does_not_retry_4xx() -> None:
    route = respx.get("https://x.test/ping").mock(
        return_value=httpx.Response(429, json={"error": {"message": "Quota exceeded"}})
    )
    with pytest.raises(ProviderClientError) as exc:
        request_json(make_client("https://x.test"), "GET", "/ping", provider="x")

    assert route.call_count == 1
    assert exc.value.status_code == 429
    assert "Quota exceeded" in str(exc.value)
    assert not exc.value.is_bad_input


@respx.mock
def test_request_json_timeout_becomes_provider_timeout() -> None:
    route = respx.get("https://x.test/ping").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeout):
        request_json(make_client("https://x.test"), "GET", "/ping", provider="x")

    assert route.call_count == 2  # one retry


@respx.mock
def test_request_json_rejects_non_json() -> None:
    respx.get("https://x.test/ping").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError, match="non-JSON"):
        request_json(make_client("https://x.test"), "GET", "/ping", provider="x", retry_once=False)


def test_record_call_outside_tracking_is_a_no_op() -> None:
    record_call("x")  # must not raise


# ---------------------------------------------------------------------------
# OSRM
# ---------------------------------------------------------------------------


@respx.mock
def test_osrm_parses_route() -> None:
    fixture = load("osrm_route.json")
    respx.get(url__startswith=f"{OSRM}/route/v1/driving/").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    route = OSRMRouter(base_url=OSRM).route(CHICAGO, DALLAS)

    assert isinstance(route, Route)
    assert route.provider == "osrm"
    assert route.distance_miles == pytest.approx(fixture["routes"][0]["distance"] / METERS_PER_MILE)
    assert route.duration_minutes == pytest.approx(fixture["routes"][0]["duration"] / 60)
    assert route.coordinates[0] == tuple(fixture["routes"][0]["geometry"]["coordinates"][0])
    assert route.geojson["type"] == "LineString"
    request = respx.calls.last.request
    assert "-87.6298,41.8781;-96.797,32.7767" in str(request.url)
    assert request.url.params["geometries"] == "geojson"


@respx.mock
def test_osrm_error_code_raises() -> None:
    respx.get(url__startswith=f"{OSRM}/route/").mock(
        return_value=httpx.Response(200, json={"code": "NoRoute", "message": "Impossible route."})
    )
    with pytest.raises(ProviderError, match="NoRoute"):
        OSRMRouter(base_url=OSRM).route(CHICAGO, DALLAS)


# ---------------------------------------------------------------------------
# ORS
# ---------------------------------------------------------------------------


@respx.mock
def test_ors_parses_directions_and_sends_truck_profile() -> None:
    fixture = load("ors_directions.json")
    mocked = respx.post(f"{ORS}/v2/directions/driving-hgv/geojson").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    route = ORSClient(api_key="k", base_url=ORS).route(CHICAGO, DALLAS)

    summary = fixture["features"][0]["properties"]["summary"]
    assert route.provider == "ors"
    assert route.distance_miles == pytest.approx(summary["distance"] / METERS_PER_MILE)
    assert route.duration_minutes == pytest.approx(summary["duration"] / 60)
    assert route.coordinates[-1] == (-96.797, 32.7767)
    request = mocked.calls.last.request
    assert request.headers["Authorization"] == "k"
    body = json.loads(request.content)
    assert body["coordinates"] == [[-87.6298, 41.8781], [-96.797, 32.7767]]
    assert body["instructions"] is False


@respx.mock
def test_ors_rejects_malformed_payload() -> None:
    respx.post(url__startswith=f"{ORS}/v2/directions/").mock(
        return_value=httpx.Response(200, json={"features": []})
    )
    with pytest.raises(ProviderError, match="unexpected"):
        ORSClient(api_key="k", base_url=ORS).route(CHICAGO, DALLAS)


@respx.mock
def test_ors_geocode_returns_us_place() -> None:
    mocked = respx.get(f"{ORS}/geocode/search").mock(
        return_value=httpx.Response(200, json=load("ors_geocode.json"))
    )

    place = ORSClient(api_key="k", base_url=ORS).geocode("1600 Pennsylvania Ave NW, Washington, DC")

    assert place == Place(
        "1600 Pennsylvania Avenue NW, Washington, DC, USA", 38.897473, -77.036548, "ors"
    )
    assert mocked.calls.last.request.url.params["boundary.country"] == "USA"


@respx.mock
def test_ors_geocode_filters_non_us_and_empty() -> None:
    client = ORSClient(api_key="k", base_url=ORS)
    respx.get(f"{ORS}/geocode/search").mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    )
    assert client.geocode("Atlantis") is None

    fixture = load("ors_geocode.json")
    fixture["features"][0]["properties"]["country_a"] = "CAN"
    respx.get(f"{ORS}/geocode/search").mock(return_value=httpx.Response(200, json=fixture))
    assert client.geocode("Toronto") is None


def test_ors_requires_api_key() -> None:
    with pytest.raises(ProviderError, match="ORS_API_KEY"):
        ORSClient(api_key="", base_url=ORS)


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------


@respx.mock
def test_nominatim_geocode_free_text() -> None:
    mocked = respx.get(f"{NOMINATIM}/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"display_name": "Dallas, Texas, United States", "lat": "32.7767", "lon": "-96.797"}
            ],
        )
    )

    place = NominatimGeocoder(base_url=NOMINATIM).geocode("Dallas, TX")

    assert place == Place("Dallas, Texas, United States", 32.7767, -96.797, "nominatim")
    assert mocked.calls.last.request.url.params["countrycodes"] == "us"
    assert "spotter-fuel-router" in mocked.calls.last.request.headers["User-Agent"]


@respx.mock
def test_nominatim_empty_result_is_none() -> None:
    respx.get(f"{NOMINATIM}/search").mock(return_value=httpx.Response(200, json=[]))

    assert NominatimGeocoder(base_url=NOMINATIM).geocode("nowhere") is None


# ---------------------------------------------------------------------------
# Fallback chains
# ---------------------------------------------------------------------------


class StubRouter:
    def __init__(self, name: str, outcome: Route | Exception) -> None:
        self.name = name
        self.outcome = outcome
        self.calls = 0

    def route(self, start: Place, finish: Place) -> Route:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


ROUTE = Route(966.9, 973.5, [(-87.63, 41.88), (-96.8, 32.78)], "stub")


def test_fallback_uses_next_provider_on_quota_or_outage() -> None:
    primary = StubRouter("ors", ProviderClientError("ors", 403, "quota"))
    secondary = StubRouter("osrm", ROUTE)
    chain = FallbackRouteProvider([primary, secondary])

    assert chain.route(CHICAGO, DALLAS) is ROUTE
    assert chain.name == "ors+osrm"
    assert (primary.calls, secondary.calls) == (1, 1)


def test_fallback_stops_on_bad_input() -> None:
    primary = StubRouter("ors", ProviderClientError("ors", 400, "bad coordinates"))
    secondary = StubRouter("osrm", ROUTE)

    with pytest.raises(ProviderClientError, match="bad coordinates"):
        FallbackRouteProvider([primary, secondary]).route(CHICAGO, DALLAS)
    assert secondary.calls == 0


def test_fallback_raises_last_error_when_all_fail() -> None:
    chain = FallbackRouteProvider(
        [StubRouter("a", ProviderTimeout("a slow")), StubRouter("b", ProviderError("b down"))]
    )
    with pytest.raises(ProviderError, match="b down"):
        chain.route(CHICAGO, DALLAS)


def test_fallback_requires_providers() -> None:
    with pytest.raises(ValueError):
        FallbackRouteProvider([])
    with pytest.raises(ValueError):
        FallbackGeocodeProvider([])


class StubGeocoder:
    def __init__(self, name: str, outcome) -> None:
        self.name, self.outcome = name, outcome

    def geocode(self, query: str):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_geocode_fallback_and_none_passthrough() -> None:
    chain = FallbackGeocodeProvider(
        [StubGeocoder("ors", ProviderError("down")), StubGeocoder("nominatim", None)]
    )
    assert chain.geocode("x") is None  # a clean "no match" from the fallback is final


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cached_route_provider_hits_inner_once() -> None:
    inner = StubRouter("stub", ROUTE)
    cached = CachedRouteProvider(inner, ttl_seconds=60)

    first = cached.route(CHICAGO, DALLAS)
    second = cached.route(CHICAGO, DALLAS)
    nudged = cached.route(Place("", 41.87812, -87.62981, "coords"), DALLAS)  # within 4 dp

    assert first == second == nudged == ROUTE
    assert inner.calls == 1


def test_cached_route_provider_distinguishes_endpoints() -> None:
    inner = StubRouter("stub", ROUTE)
    cached = CachedRouteProvider(inner, ttl_seconds=60)
    cached.route(CHICAGO, DALLAS)
    cached.route(DALLAS, CHICAGO)

    assert inner.calls == 2


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def test_route_provider_factory_builds_chain_from_settings(settings) -> None:
    settings.ROUTING_PROVIDERS = ["ors", "osrm"]
    settings.ORS_API_KEY = "k"
    reset_providers()

    provider = get_route_provider()

    assert isinstance(provider, CachedRouteProvider)
    assert provider.name == "ors+osrm"
    assert get_route_provider() is provider  # singleton


def test_route_provider_factory_skips_ors_without_key(settings) -> None:
    settings.ROUTING_PROVIDERS = ["ors", "osrm"]
    settings.ORS_API_KEY = ""
    reset_providers()

    assert get_route_provider().name == "osrm"


def test_factories_reject_unknown_and_empty(settings) -> None:
    settings.ROUTING_PROVIDERS = ["mapquest"]
    reset_providers()
    with pytest.raises(ValueError, match="unknown routing provider"):
        get_route_provider()

    settings.GEOCODING_PROVIDERS = ["ors"]
    settings.ORS_API_KEY = ""
    reset_providers()
    with pytest.raises(ValueError, match="no usable geocoding provider"):
        get_geocode_provider()


def test_geocode_provider_factory(settings) -> None:
    settings.GEOCODING_PROVIDERS = ["nominatim"]
    reset_providers()

    assert get_geocode_provider().name == "nominatim"
