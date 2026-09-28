"""Shared in-memory stand-ins for the resolver, router and station catalog."""

from __future__ import annotations

import numpy as np

from apps.planner.geo import MILES_PER_DEG_LAT
from apps.routing.base import Place, Route
from apps.routing.resolver import LocationNotFound
from apps.stations.index import StationCatalog, StationInfo

LAT = 40.0
MILES_PER_DEG_LNG = MILES_PER_DEG_LAT * np.cos(np.radians(LAT))
START = Place("Start, IL, USA", LAT, -90.0, "local", state="IL")
FINISH = Place("Finish, OH, USA", LAT, -80.0, "local", state="OH")


def synthetic_route() -> Route:
    """A straight line east along latitude 40, ~530 miles, 201 vertices."""
    lngs = np.linspace(-90.0, -80.0, 201)
    return Route(
        distance_miles=float(10 * MILES_PER_DEG_LNG),
        duration_minutes=600.0,
        coordinates=[(float(x), LAT) for x in lngs],
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


def default_catalog() -> StationCatalog:
    """Stations at ~0, ~132, ~265, ~397 and ~530 miles; one 20 miles off-route; one far away."""
    return StationCatalog(
        [
            station(1, -89.98, 3.50),
            station(2, -87.5, 3.00),
            station(3, -85.0, 3.40),
            station(4, -82.5, 2.80),
            station(5, -80.02, 3.90),
            station(6, -85.0, 1.00, lat=LAT + 20 / MILES_PER_DEG_LAT),
            station(7, -100.0, 1.00),
        ]
    )


class StubResolver:
    def __init__(self) -> None:
        self.places = {"Start, IL": START, "Finish, OH": FINISH}
        self.fail: Exception | None = None

    def resolve(self, raw: str, state: str | None = None) -> Place:
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
