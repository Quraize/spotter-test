"""
The one use case: plan a trip.

    resolve start & finish -> route -> candidates along the corridor -> optimise stops

Everything here is synchronous and provider-agnostic; the view only validates input and
serialises the result. Timings and the external-call log are captured so the response can
show how the time was spent and how many upstream calls were made.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from django.conf import settings

from apps.planner.corridor import RouteGeometry, find_candidates
from apps.planner.exceptions import NoStationsOnRoute
from apps.planner.geo import haversine_miles
from apps.planner.optimizer import plan_fuel_stops
from apps.planner.types import FuelPlan, FuelStop, Vehicle
from apps.routing.base import Place, Route
from apps.routing.calls import track_calls
from apps.routing.providers import get_resolver, get_route_provider
from apps.stations.index import StationCatalog, StationInfo, get_station_catalog


@dataclass(frozen=True, slots=True)
class PlanRequest:
    start: str
    finish: str
    start_state: str | None = None
    finish_state: str | None = None
    initial_fuel_miles: float = 0.0
    stop_penalty: float = 10.0
    corridor_miles: float = 10.0
    include_geometry: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedPlace:
    query: str
    place: Place


@dataclass(frozen=True, slots=True)
class StopDetail:
    """A FuelStop joined with its station record, ready for serialisation."""

    order: int
    station: StationInfo
    stop: FuelStop


@dataclass(slots=True)
class PlanResult:
    request: PlanRequest
    start: ResolvedPlace
    finish: ResolvedPlace
    route: Route
    vehicle: Vehicle
    plan: FuelPlan
    stops: list[StopDetail]
    pre_trip: StopDetail | None
    candidate_count: int
    external_calls: list[str]
    timings_ms: dict[str, float] = field(default_factory=dict)


def plan_route(req: PlanRequest) -> PlanResult:
    vehicle = Vehicle(range_miles=settings.VEHICLE_RANGE_MILES, mpg=settings.VEHICLE_MPG)
    timings: dict[str, float] = {}
    resolver = get_resolver()
    router = get_route_provider()
    catalog: StationCatalog = get_station_catalog()

    with track_calls() as calls:
        with _timed(timings, "resolve"):
            start = ResolvedPlace(req.start, resolver.resolve(req.start, req.start_state))
            finish = ResolvedPlace(req.finish, resolver.resolve(req.finish, req.finish_state))

        with _timed(timings, "route"):
            if _same_place(start.place, finish.place):
                # Nothing to drive: skip the routing provider entirely.
                route = Route(0.0, 0.0, [start.place.lnglat, finish.place.lnglat], "none")
            else:
                route = router.route(start.place, finish.place)

        with _timed(timings, "corridor"):
            geometry = RouteGeometry(route.coordinates, total_miles=route.distance_miles)
            candidates = find_candidates(geometry, catalog.index, req.corridor_miles)

        with _timed(timings, "optimize"):
            if not candidates and geometry.total_miles > req.initial_fuel_miles:
                raise NoStationsOnRoute(geometry.total_miles, req.corridor_miles)
            plan = plan_fuel_stops(
                geometry.total_miles,
                candidates,
                vehicle,
                initial_fuel_miles=req.initial_fuel_miles,
                stop_penalty=req.stop_penalty,
            )

    stops = [
        StopDetail(order=i, station=catalog.get(s.candidate.station_id), stop=s)
        for i, s in enumerate(plan.stops, start=1)
    ]
    pre_trip = (
        StopDetail(
            order=0, station=catalog.get(plan.pre_trip.candidate.station_id), stop=plan.pre_trip
        )
        if plan.pre_trip
        else None
    )
    return PlanResult(
        request=req,
        start=start,
        finish=finish,
        route=route,
        vehicle=vehicle,
        plan=plan,
        stops=stops,
        pre_trip=pre_trip,
        candidate_count=len(candidates),
        external_calls=list(calls),
        timings_ms=timings,
    )


SAME_PLACE_MILES = 0.05


def _same_place(a: Place, b: Place) -> bool:
    return float(haversine_miles(a.lat, a.lng, b.lat, b.lng)) < SAME_PLACE_MILES


class _timed:
    def __init__(self, sink: dict[str, float], key: str) -> None:
        self.sink, self.key = sink, key

    def __enter__(self) -> None:
        self._t = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        self.sink[self.key] = round((time.perf_counter() - self._t) * 1000, 1)
