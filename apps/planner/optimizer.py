"""
Cost-optimal fuel purchasing along a fixed route ("the gas station problem").

Two solvers share one model:

``plan_fuel_stops``          production solver. Exact dynamic program that minimises
                             fuel cost + stop_penalty x number of stops. With a zero
                             penalty it returns the pure cost optimum.
``plan_fuel_stops_greedy``   reference solver. The Khuller/Malekian/Mestre greedy for
                             fractional fill-ups (ESA 2007), optimal when stops are free.
                             Kept for cross-checking the DP in tests.

Model
-----
Fuel is tracked in miles of range and converted to gallons via the vehicle's mpg at the end.
Stations are points on the route at increasing mile markers. The vehicle may buy any
fractional amount at a station, never exceeds its range, and never runs dry.

Structural fact used by the DP (Khuller et al., Lemma 2.1): some optimal plan buys, at every
stop, either exactly enough to reach the next stop (arriving empty) or a full tank. So the
fuel on arrival at any station is one of: 0, ``range - distance`` from an earlier fill-up,
or what was left of the departure fuel. That bounds the state space to O(n x window).

Departure fuel: the caller passes ``initial_fuel_miles``. If that cannot reach the first
station on the route, the shortfall is treated as a purchase made before departure at the
first station's price and reported separately as ``FuelPlan.pre_trip``. With the default of
0 every mile of the trip is paid for, which is what "total money spent on fuel" means.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from apps.planner.exceptions import NoStationsOnRoute, StationGapTooLarge
from apps.planner.types import Candidate, FuelPlan, FuelStop, Vehicle

EPS = 1e-9
# Among plans with (numerically) equal cost, prefer fewer stops.
_TIE_BREAK_PENALTY = 1e-7
# Purchases below this are artefacts of stations sharing a mile marker (e.g. two truck stops
# at the same exit); they are folded into a neighbouring purchase so no "0.00 gallon" stop
# is ever reported. Fuel accounting stays exact.
MIN_PURCHASE_MILES = 0.1


@dataclass(slots=True)
class _Purchase:
    candidate: Candidate
    arrival_miles: float
    bought_miles: float


@dataclass(slots=True)
class _Setup:
    """Validated, normalised inputs shared by both solvers."""

    trip_miles: float
    vehicle: Vehicle
    initial_fuel_miles: float
    cands: list[Candidate]
    pre_trip: _Purchase | None
    departure_fuel: float  # miles of range when leaving the origin (after pre-trip top-up)


def _setup(
    trip_miles: float,
    candidates: Iterable[Candidate],
    vehicle: Vehicle | None,
    initial_fuel_miles: float,
) -> _Setup | FuelPlan:
    vehicle = vehicle or Vehicle()
    if trip_miles < 0:
        raise ValueError("trip_miles must be non-negative")
    if not 0 <= initial_fuel_miles <= vehicle.range_miles + EPS:
        raise ValueError("initial_fuel_miles must be between 0 and the vehicle range")

    cands = sorted(
        (c for c in candidates if -EPS <= c.mile_marker <= trip_miles + EPS),
        key=lambda c: (c.mile_marker, c.price),
    )
    if trip_miles <= initial_fuel_miles + EPS:
        leftover = initial_fuel_miles - trip_miles
        return _finish(trip_miles, vehicle, initial_fuel_miles, None, [], leftover)
    if not cands:
        raise NoStationsOnRoute(trip_miles)

    first = cands[0]
    pre_trip: _Purchase | None = None
    fuel = initial_fuel_miles
    if first.mile_marker > fuel + EPS:
        if first.mile_marker > vehicle.range_miles + EPS:
            raise StationGapTooLarge(0.0, first.mile_marker, vehicle.range_miles)
        pre_trip = _Purchase(first, arrival_miles=fuel, bought_miles=first.mile_marker - fuel)
        fuel = first.mile_marker
    return _Setup(trip_miles, vehicle, initial_fuel_miles, cands, pre_trip, fuel)


# ---------------------------------------------------------------------------
# Production solver: exact DP with a per-stop penalty
# ---------------------------------------------------------------------------

# A step is one purchase: (station index, fuel on arrival, miles bought, how we got there).
# `ref` is ("start",) | ("zero", station) | ("fill", station) and names the table that holds
# the previous step, so the plan can be reconstructed backwards.
_Step = tuple[int, float, float, tuple]


def plan_fuel_stops(
    trip_miles: float,
    candidates: Iterable[Candidate],
    vehicle: Vehicle | None = None,
    initial_fuel_miles: float = 0.0,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    """
    Minimise fuel cost + ``stop_penalty`` per stop. The reported plan cost is fuel only.

    ``stop_penalty`` is in dollars and models the driver's time per stop; it is what stops the
    optimum from being "pull in at every marginally cheaper station and buy two gallons".
    """
    if stop_penalty < 0:
        raise ValueError("stop_penalty must be non-negative")
    setup = _setup(trip_miles, candidates, vehicle, initial_fuel_miles)
    if isinstance(setup, FuelPlan):
        return setup

    cands, vehicle = setup.cands, setup.vehicle
    trip, rng, f_dep = setup.trip_miles, vehicle.range_miles, setup.departure_fuel
    n = len(cands)
    miles = [c.mile_marker for c in cands]
    per_mile = [c.price / vehicle.mpg for c in cands]  # $ per mile of range
    penalty = stop_penalty + _TIE_BREAK_PENALTY
    inf = math.inf

    # zero[j]: best cost arriving at j empty; fill[i]: best cost to *leave* i with a full tank.
    zero_cost = [inf] * n
    zero_step: list[_Step | None] = [None] * n
    fill_cost = [inf] * n
    fill_step: list[_Step | None] = [None] * n
    finish_cost = inf
    finish_step: _Step | None = None
    lo = 0  # first station within range behind j (monotone as j advances)

    for j in range(n):
        m_j, c_j = miles[j], per_mile[j]
        while miles[j] - miles[lo] > rng + EPS:
            lo += 1

        # Every way of arriving at j: (fuel on arrival, cost so far, ref).
        states: list[tuple[float, float, tuple]] = []
        if zero_cost[j] < inf:
            states.append((0.0, zero_cost[j], ("zero", j)))
        for i in range(lo, j):
            if fill_cost[i] < inf:
                states.append((rng - (m_j - miles[i]), fill_cost[i], ("fill", i)))
        if m_j <= f_dep + EPS:
            states.append((f_dep - m_j, 0.0, ("start",)))
        if not states:
            continue
        states.sort(key=lambda s: s[0])

        # Reaching the finish from j, with or without buying here.
        remaining = trip - m_j
        for g, cost, ref in states:
            if remaining <= g + EPS and cost < finish_cost:
                finish_cost, finish_step = cost, (j, g, 0.0, ref)
        if remaining <= rng + EPS:
            for g, cost, ref in states:
                if g < remaining - EPS:
                    total = cost + (remaining - g) * c_j + penalty
                    if total < finish_cost:
                        finish_cost, finish_step = total, (j, g, remaining - g, ref)

        # Filling up at j.
        for g, cost, ref in states:
            if g < rng - EPS:
                total = cost + (rng - g) * c_j + penalty
                if total < fill_cost[j]:
                    fill_cost[j], fill_step[j] = total, (j, g, rng - g, ref)

        # Buying just enough at j to reach a later station k empty. As k moves out, more
        # arrival states qualify (those with g < distance); keep a running best.
        ptr, best_base, best_state = 0, inf, None
        for k in range(j + 1, n):
            d = miles[k] - m_j
            if d > rng + EPS:
                break
            while ptr < len(states) and states[ptr][0] < d - EPS:
                g, cost, ref = states[ptr]
                base = cost - g * c_j
                if base < best_base:
                    best_base, best_state = base, states[ptr]
                ptr += 1
            if best_state is not None:
                total = best_base + d * c_j + penalty
                if total < zero_cost[k]:
                    g, _, ref = best_state
                    zero_cost[k], zero_step[k] = total, (j, g, d - g, ref)

    if finish_step is None:
        _raise_gap(cands, trip, rng, f_dep)

    # Walk the steps backwards to recover the purchases.
    purchases: list[_Purchase] = []
    step: _Step | None = finish_step
    leftover = 0.0
    while step is not None:
        idx, g, bought, ref = step
        if step is finish_step and bought <= EPS:
            leftover = g - (trip - miles[idx])
        if bought > EPS:
            purchases.append(_Purchase(cands[idx], arrival_miles=g, bought_miles=bought))
        kind = ref[0]
        if kind == "start":
            step = None
        elif kind == "zero":
            step = zero_step[ref[1]]
        else:
            step = fill_step[ref[1]]
    purchases.reverse()
    return _finish(trip, vehicle, setup.initial_fuel_miles, setup.pre_trip, purchases, leftover)


def _raise_gap(cands: list[Candidate], trip: float, rng: float, f_dep: float) -> None:
    """The DP found no plan: name the first gap wider than the range."""
    miles = [c.mile_marker for c in cands]
    prev = 0.0 if f_dep >= miles[0] - EPS else None
    for m in miles:
        if prev is not None and m - prev > rng + EPS:
            raise StationGapTooLarge(prev, m, rng)
        prev = m if prev is None or m > prev else prev
    if trip - miles[-1] > rng + EPS:
        raise StationGapTooLarge(miles[-1], trip, rng)
    raise StationGapTooLarge(0.0, trip, rng)  # pragma: no cover - defensive


# ---------------------------------------------------------------------------
# Reference solver: greedy, optimal when stops are free
# ---------------------------------------------------------------------------


def plan_fuel_stops_greedy(
    trip_miles: float,
    candidates: Iterable[Candidate],
    vehicle: Vehicle | None = None,
    initial_fuel_miles: float = 0.0,
) -> FuelPlan:
    """
    At each station: if a cheaper station is reachable, buy just enough to reach the nearest
    cheaper one; else if the finish is reachable, buy just enough to finish; else fill up and
    drive to the cheapest reachable station.
    """
    setup = _setup(trip_miles, candidates, vehicle, initial_fuel_miles)
    if isinstance(setup, FuelPlan):
        return setup

    cands, vehicle = setup.cands, setup.vehicle
    trip, rng = setup.trip_miles, vehicle.range_miles
    purchases: list[_Purchase] = []
    leftover = 0.0
    i = 0
    arrival = setup.departure_fuel - cands[0].mile_marker
    n = len(cands)

    while True:
        here = cands[i]
        remaining = trip - here.mile_marker

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
            next_mile = cands[i + 1].mile_marker if i + 1 < n else trip
            raise StationGapTooLarge(here.mile_marker, next_mile, rng)
        target = cands[cheapest]
        bought = rng - arrival
        _record(purchases, here, arrival, bought)
        arrival = rng - (target.mile_marker - here.mile_marker)
        i = cheapest

    return _finish(trip, vehicle, setup.initial_fuel_miles, setup.pre_trip, purchases, leftover)


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


# ---------------------------------------------------------------------------
# Shared result assembly
# ---------------------------------------------------------------------------


def _to_stop(p: _Purchase, vehicle: Vehicle) -> FuelStop:
    gallons = vehicle.gallons(p.bought_miles)
    return FuelStop(
        candidate=p.candidate,
        gallons=gallons,
        cost=gallons * p.candidate.price,
        fuel_on_arrival_gallons=vehicle.gallons(p.arrival_miles),
        fuel_on_departure_gallons=vehicle.gallons(p.arrival_miles + p.bought_miles),
    )


def _merge_negligible(
    pre_trip: _Purchase | None, purchases: list[_Purchase]
) -> tuple[_Purchase | None, list[_Purchase]]:
    """Fold sub-0.1-mile purchases into the next (or previous) purchase."""
    if pre_trip is not None and pre_trip.bought_miles < MIN_PURCHASE_MILES and purchases:
        first = purchases[0]
        first.bought_miles += pre_trip.bought_miles
        first.arrival_miles = max(0.0, first.arrival_miles - pre_trip.bought_miles)
        pre_trip = None
    kept: list[_Purchase] = []
    carry = 0.0
    for p in purchases:
        if p.bought_miles + carry < MIN_PURCHASE_MILES and p is not purchases[-1]:
            carry += p.bought_miles
            continue
        p.bought_miles += carry
        p.arrival_miles = max(0.0, p.arrival_miles - carry)
        carry = 0.0
        kept.append(p)
    if len(kept) >= 2 and kept[-1].bought_miles < MIN_PURCHASE_MILES:
        kept[-2].bought_miles += kept.pop().bought_miles
    return pre_trip, kept


def _finish(
    trip_miles: float,
    vehicle: Vehicle,
    initial_fuel_miles: float,
    pre_trip: _Purchase | None,
    purchases: list[_Purchase],
    leftover_miles: float,
) -> FuelPlan:
    pre_trip, purchases = _merge_negligible(pre_trip, purchases)
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
