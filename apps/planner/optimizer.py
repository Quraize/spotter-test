"""
Cost-optimal fuel purchasing along a fixed route.

This is the "gas station problem" with fractional purchases (Khuller, Malekian & Mestre,
"To Fill or Not to Fill: The Gas Station Problem", ESA 2007). The greedy below is optimal:

    At each station:
      1. If a cheaper station is reachable on a full tank, buy just enough to reach the
         *nearest* cheaper one (never carry expensive fuel past cheaper fuel).
      2. Otherwise, if the finish is reachable, buy just enough to finish.
      3. Otherwise fill the tank and drive to the *cheapest* reachable station.

Fuel is tracked in miles of range; gallons are derived at the end via the vehicle's mpg.

Departure fuel: the caller passes ``initial_fuel_miles``. If that is not enough to reach the
first station on the route, the shortfall is treated as a purchase made before departure at
the first station's price and reported separately as ``FuelPlan.pre_trip``. With the default
of 0 every mile of the trip is paid for, which is what "total money spent on fuel" means.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from apps.planner.exceptions import NoStationsOnRoute, StationGapTooLarge
from apps.planner.types import Candidate, FuelPlan, FuelStop, Vehicle

EPS = 1e-9


@dataclass(slots=True)
class _Purchase:
    candidate: Candidate
    arrival_miles: float
    bought_miles: float


def plan_fuel_stops(
    trip_miles: float,
    candidates: Iterable[Candidate],
    vehicle: Vehicle | None = None,
    initial_fuel_miles: float = 0.0,
) -> FuelPlan:
    vehicle = vehicle or Vehicle()
    if trip_miles < 0:
        raise ValueError("trip_miles must be non-negative")
    if not 0 <= initial_fuel_miles <= vehicle.range_miles + EPS:
        raise ValueError("initial_fuel_miles must be between 0 and the vehicle range")

    rng = vehicle.range_miles
    cands = sorted(
        (c for c in candidates if -EPS <= c.mile_marker <= trip_miles + EPS),
        key=lambda c: (c.mile_marker, c.price),
    )

    if trip_miles <= initial_fuel_miles + EPS:
        leftover = initial_fuel_miles - trip_miles
        return _finish(trip_miles, vehicle, initial_fuel_miles, None, [], leftover)
    if not cands:
        raise NoStationsOnRoute(trip_miles)

    # Departure rule: any shortfall to the first station is bought before leaving, at its price.
    first = cands[0]
    pre_trip: _Purchase | None = None
    fuel = initial_fuel_miles
    if first.mile_marker > fuel + EPS:
        if first.mile_marker > rng + EPS:
            raise StationGapTooLarge(0.0, first.mile_marker, rng)
        pre_trip = _Purchase(first, arrival_miles=fuel, bought_miles=first.mile_marker - fuel)
        fuel = first.mile_marker

    purchases: list[_Purchase] = []
    leftover = 0.0
    i = 0
    arrival = fuel - first.mile_marker
    n = len(cands)

    while True:
        here = cands[i]
        remaining = trip_miles - here.mile_marker

        if remaining <= arrival + EPS:
            leftover = arrival - remaining
            break

        reach = here.mile_marker + rng
        nearest_cheaper = _nearest_cheaper(cands, i, reach)

        if nearest_cheaper is not None:
            target = cands[nearest_cheaper]
            distance = target.mile_marker - here.mile_marker
            bought = max(0.0, distance - arrival)
            _record(purchases, here, arrival, bought)
            arrival = arrival + bought - distance
            i = nearest_cheaper
            continue

        if remaining <= rng + EPS:
            _record(purchases, here, arrival, remaining - arrival)
            leftover = 0.0
            break

        cheapest = _cheapest_reachable(cands, i, reach)
        if cheapest is None:
            next_mile = cands[i + 1].mile_marker if i + 1 < n else trip_miles
            raise StationGapTooLarge(here.mile_marker, next_mile, rng)
        target = cands[cheapest]
        bought = rng - arrival
        _record(purchases, here, arrival, bought)
        arrival = rng - (target.mile_marker - here.mile_marker)
        i = cheapest

    return _finish(trip_miles, vehicle, initial_fuel_miles, pre_trip, purchases, leftover)


def _nearest_cheaper(cands: list[Candidate], i: int, reach: float) -> int | None:
    price = cands[i].price
    for k in range(i + 1, len(cands)):
        if cands[k].mile_marker > reach + EPS:
            return None
        if cands[k].price < price - EPS:
            return k
    return None


def _cheapest_reachable(cands: list[Candidate], i: int, reach: float) -> int | None:
    best: int | None = None
    for k in range(i + 1, len(cands)):
        c = cands[k]
        if c.mile_marker > reach + EPS:
            break
        # Lowest price; on ties prefer the farther station (fewer stops overall).
        if best is None or (c.price, -c.mile_marker) < (
            cands[best].price,
            -cands[best].mile_marker,
        ):
            best = k
    return best


def _record(purchases: list[_Purchase], at: Candidate, arrival: float, bought: float) -> None:
    if bought > EPS:
        purchases.append(_Purchase(at, arrival_miles=arrival, bought_miles=bought))


def _to_stop(p: _Purchase, vehicle: Vehicle) -> FuelStop:
    gallons = vehicle.gallons(p.bought_miles)
    return FuelStop(
        candidate=p.candidate,
        gallons=gallons,
        cost=gallons * p.candidate.price,
        fuel_on_arrival_gallons=vehicle.gallons(p.arrival_miles),
        fuel_on_departure_gallons=vehicle.gallons(p.arrival_miles + p.bought_miles),
    )


def _finish(
    trip_miles: float,
    vehicle: Vehicle,
    initial_fuel_miles: float,
    pre_trip: _Purchase | None,
    purchases: list[_Purchase],
    leftover_miles: float,
) -> FuelPlan:
    stops = tuple(_to_stop(p, vehicle) for p in purchases)
    pre = _to_stop(pre_trip, vehicle) if pre_trip else None
    all_purchases = (*stops, pre) if pre else stops
    return FuelPlan(
        trip_miles=trip_miles,
        vehicle=vehicle,
        initial_fuel_gallons=vehicle.gallons(initial_fuel_miles),
        pre_trip=pre,
        stops=stops,
        total_gallons=sum(s.gallons for s in all_purchases),
        total_cost=sum(s.cost for s in all_purchases),
        fuel_at_finish_gallons=vehicle.gallons(max(0.0, leftover_miles)),
    )
