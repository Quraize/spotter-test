"""
Optimizer tests.

Both solvers (the production DP and the reference greedy) run through the same unit cases.
The random-instance tests compare each against an exact dynamic program over integer fuel
levels. For integer positions and range an integral optimum exists (the fuel constraints
form a consecutive-ones system), so that DP is a true oracle, with or without a stop penalty.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

import pytest

from apps.planner.exceptions import NoStationsOnRoute, StationGapTooLarge
from apps.planner.optimizer import plan_fuel_stops, plan_fuel_stops_greedy
from apps.planner.types import Candidate, FuelPlan, Vehicle

V = Vehicle(range_miles=500, mpg=10)

Solver = Callable[..., FuelPlan]


@pytest.fixture(params=[plan_fuel_stops, plan_fuel_stops_greedy], ids=["dp", "greedy"])
def solve(request: pytest.FixtureRequest) -> Solver:
    return request.param


def cand(station_id: int, mile: float, price: float) -> Candidate:
    return Candidate(
        station_id=station_id, price=price, mile_marker=mile, detour_miles=0.0, lat=0.0, lng=0.0
    )


def ids(plan: FuelPlan) -> list[int]:
    return [s.candidate.station_id for s in plan.stops]


# ---------------------------------------------------------------------------
# Behaviour shared by both solvers (stops are free)
# ---------------------------------------------------------------------------


def test_no_stops_when_initial_fuel_covers_trip(solve: Solver) -> None:
    plan = solve(300, [cand(1, 50, 3.0)], V, initial_fuel_miles=500)

    assert plan.stops == ()
    assert plan.total_cost == 0
    assert plan.fuel_at_finish_gallons == pytest.approx(20.0)


def test_no_stations_raises(solve: Solver) -> None:
    with pytest.raises(NoStationsOnRoute):
        solve(300, [], V)


def test_single_station_empty_start_pays_for_whole_trip(solve: Solver) -> None:
    plan = solve(300, [cand(1, 5, 3.0)], V)

    assert ids(plan) == [1]
    stop = plan.stops[0]
    assert stop.gallons == pytest.approx(29.5)  # 295 miles to finish
    assert stop.cost == pytest.approx(88.5)
    assert stop.fuel_on_arrival_gallons == pytest.approx(0.0)
    assert stop.fuel_on_departure_gallons == pytest.approx(29.5)
    # The 5 miles needed to reach the first station are bought before leaving, at its price.
    assert plan.pre_trip is not None
    assert plan.pre_trip.candidate.station_id == 1
    assert plan.pre_trip.gallons == pytest.approx(0.5)
    assert plan.pre_trip.cost == pytest.approx(1.5)
    assert plan.total_gallons == pytest.approx(plan.gallons_consumed)
    assert plan.total_cost == pytest.approx(90.0)
    assert plan.fuel_at_finish_gallons == pytest.approx(0.0)


def test_buys_just_enough_to_reach_cheaper_station(solve: Solver) -> None:
    plan = solve(400, [cand(1, 0, 4.0), cand(2, 200, 3.0)], V)

    assert ids(plan) == [1, 2]
    assert [s.gallons for s in plan.stops] == pytest.approx([20.0, 20.0])
    assert plan.total_cost == pytest.approx(80.0 + 60.0)


def test_fills_up_when_nothing_cheaper_is_reachable(solve: Solver) -> None:
    plan = solve(900, [cand(1, 0, 3.0), cand(2, 400, 3.5), cand(3, 800, 3.2)], V)

    assert ids(plan) == [1, 2, 3]
    assert [s.gallons for s in plan.stops] == pytest.approx([50.0, 30.0, 10.0])
    assert plan.stops[1].fuel_on_arrival_gallons == pytest.approx(10.0)
    assert plan.total_cost == pytest.approx(150.0 + 105.0 + 32.0)


def test_passes_expensive_station_without_buying(solve: Solver) -> None:
    plan = solve(700, [cand(1, 100, 5.0), cand(2, 300, 2.0)], V, initial_fuel_miles=500)

    assert ids(plan) == [2]
    assert plan.stops[0].gallons == pytest.approx(20.0)
    assert plan.total_cost == pytest.approx(40.0)


def test_buys_just_enough_to_finish_when_finish_is_reachable(solve: Solver) -> None:
    plan = solve(450, [cand(1, 0, 3.0), cand(2, 300, 3.5)], V)

    assert ids(plan) == [1]
    assert plan.stops[0].gallons == pytest.approx(45.0)
    assert plan.fuel_at_finish_gallons == pytest.approx(0.0)


def test_departure_shortfall_is_charged_at_first_station_price(solve: Solver) -> None:
    plan = solve(250, [cand(1, 250, 3.0)], V, initial_fuel_miles=100)

    assert plan.stops == ()  # nothing pumped en route: the station is at the finish
    assert plan.pre_trip is not None
    assert plan.pre_trip.gallons == pytest.approx(15.0)  # 150 mile shortfall
    assert plan.pre_trip.fuel_on_arrival_gallons == pytest.approx(10.0)
    assert plan.pre_trip.fuel_on_departure_gallons == pytest.approx(25.0)
    assert plan.total_cost == pytest.approx(45.0)


def test_no_pre_trip_purchase_when_initial_fuel_reaches_first_station(solve: Solver) -> None:
    plan = solve(400, [cand(1, 50, 3.0)], V, initial_fuel_miles=100)

    assert plan.pre_trip is None
    assert plan.stops[0].fuel_on_arrival_gallons == pytest.approx(5.0)
    assert plan.stops[0].gallons == pytest.approx(30.0)


def test_first_station_beyond_range_raises(solve: Solver) -> None:
    with pytest.raises(StationGapTooLarge) as exc:
        solve(700, [cand(1, 600, 3.0)], V)

    assert (exc.value.from_mile, exc.value.to_mile) == (0.0, 600.0)


def test_gap_between_stations_beyond_range_raises(solve: Solver) -> None:
    with pytest.raises(StationGapTooLarge) as exc:
        solve(1200, [cand(1, 0, 3.0), cand(2, 650, 3.0)], V)

    assert (exc.value.from_mile, exc.value.to_mile) == (0.0, 650.0)


def test_gap_to_finish_beyond_range_raises(solve: Solver) -> None:
    with pytest.raises(StationGapTooLarge) as exc:
        solve(1200, [cand(1, 0, 3.0), cand(2, 400, 3.0)], V)

    assert (exc.value.from_mile, exc.value.to_mile) == (400.0, 1200.0)


def test_same_mile_marker_prefers_cheaper_station(solve: Solver) -> None:
    plan = solve(300, [cand(1, 100, 4.0), cand(2, 100, 3.0)], V)

    assert ids(plan) == [2]


def test_candidates_outside_trip_are_ignored(solve: Solver) -> None:
    plan = solve(300, [cand(9, -5, 1.0), cand(1, 10, 3.0), cand(8, 301, 1.0)], V)

    assert ids(plan) == [1]


def test_invalid_arguments(solve: Solver) -> None:
    with pytest.raises(ValueError):
        solve(-1, [], V)
    with pytest.raises(ValueError):
        solve(10, [], V, initial_fuel_miles=501)
    with pytest.raises(ValueError):
        Vehicle(range_miles=0)


def test_greedy_ties_prefer_farther_station_when_filling_up() -> None:
    plan = plan_fuel_stops_greedy(900, [cand(1, 0, 3.0), cand(2, 200, 3.5), cand(3, 400, 3.5)], V)

    assert ids(plan) == [1, 3]
    assert plan.stops[1].gallons == pytest.approx(40.0)


def test_dp_tie_has_same_cost_and_stop_count() -> None:
    plan = plan_fuel_stops(900, [cand(1, 0, 3.0), cand(2, 200, 3.5), cand(3, 400, 3.5)], V)

    assert plan.stop_count == 2
    assert plan.total_cost == pytest.approx(150.0 + 140.0)


# ---------------------------------------------------------------------------
# Stop penalty (DP only)
# ---------------------------------------------------------------------------

STAIRCASE = [cand(1, 0, 3.0), cand(2, 100, 2.9), cand(3, 200, 2.8)]


def test_zero_penalty_chases_every_cheaper_station() -> None:
    plan = plan_fuel_stops(300, STAIRCASE, V, stop_penalty=0.0)

    assert ids(plan) == [1, 2, 3]
    assert plan.total_cost == pytest.approx(30.0 + 29.0 + 28.0)


def test_small_penalty_keeps_all_stops_when_savings_exceed_it() -> None:
    plan = plan_fuel_stops(300, STAIRCASE, V, stop_penalty=0.5)

    assert ids(plan) == [1, 2, 3]


def test_large_penalty_collapses_to_one_stop() -> None:
    plan = plan_fuel_stops(300, STAIRCASE, V, stop_penalty=5.0)

    assert ids(plan) == [1]
    assert plan.stops[0].gallons == pytest.approx(30.0)
    assert plan.total_cost == pytest.approx(90.0)  # fuel only, penalty is not reported


def test_penalty_never_makes_an_infeasible_plan() -> None:
    plan = plan_fuel_stops(
        1400, [cand(1, 0, 3.0), cand(2, 450, 3.4), cand(3, 900, 3.1)], V, stop_penalty=1000
    )

    assert ids(plan) == [1, 2, 3]  # forced by the 500 mile range whatever the penalty
    assert plan.total_gallons == pytest.approx(140.0)


def test_negative_penalty_rejected() -> None:
    with pytest.raises(ValueError):
        plan_fuel_stops(10, [cand(1, 0, 3.0)], V, stop_penalty=-1)


# ---------------------------------------------------------------------------
# Exact oracle
# ---------------------------------------------------------------------------


def brute_force_cost(
    trip: int, stations: list[tuple[int, float]], rng: int, init: int, penalty: float = 0.0
) -> float:
    """Exact minimum of fuel cost (price x miles) + penalty per stop, via DP over integer fuel."""
    stations = sorted(stations)
    first_mile, first_price = stations[0]
    pre_cost, fuel0 = 0.0, init
    if first_mile > init:
        pre_cost, fuel0 = (first_mile - init) * first_price, first_mile

    nodes: list[tuple[int, float | None]] = [(0, None), *stations, (trip, None)]
    dp: dict[int, float] = {fuel0: 0.0}
    for k in range(1, len(nodes)):
        d = nodes[k][0] - nodes[k - 1][0]
        price = nodes[k][1]
        nxt: dict[int, float] = {}
        for f, c in dp.items():
            if f < d:
                continue
            a = f - d
            if price is None:
                nxt[a] = min(nxt.get(a, math.inf), c)
            else:
                nxt[a] = min(nxt.get(a, math.inf), c)
                for g in range(1, rng - a + 1):
                    nxt[a + g] = min(nxt.get(a + g, math.inf), c + g * price + penalty)
        dp = nxt
    return pre_cost + min(dp.values())


def random_feasible_instance(r: random.Random):
    while True:
        rng = r.randint(3, 25)
        trip = r.randint(1, 60)
        n = r.randint(1, 8)
        miles = sorted(r.randint(0, trip) for _ in range(n))
        if miles[0] > rng or trip - miles[-1] > rng:
            continue
        if any(b - a > rng for a, b in zip(miles, miles[1:], strict=False)):
            continue
        prices = [float(r.choice([1, 2, 3, 4, 5])) for _ in range(n)]
        init = r.randint(0, rng)
        return trip, list(zip(miles, prices, strict=True)), rng, init


def check_invariants(plan: FuelPlan, rng: float) -> None:
    assert plan.initial_fuel_gallons + plan.total_gallons == pytest.approx(
        plan.gallons_consumed + plan.fuel_at_finish_gallons, abs=1e-6
    )
    for s in plan.stops:
        assert -1e-9 <= s.fuel_on_arrival_gallons <= rng + 1e-9
        assert s.fuel_on_departure_gallons <= rng + 1e-9
        assert s.gallons > 0
        assert s.cost == pytest.approx(s.gallons * s.price)
    if plan.pre_trip:
        assert plan.pre_trip.fuel_on_departure_gallons == pytest.approx(
            plan.pre_trip.candidate.mile_marker
        )
    markers = [s.mile_marker for s in plan.stops]
    assert markers == sorted(markers)
    assert plan.total_cost == pytest.approx(
        sum(s.cost for s in plan.stops) + (plan.pre_trip.cost if plan.pre_trip else 0.0)
    )


def test_greedy_matches_exact_oracle_on_random_instances() -> None:
    r = random.Random(20260928)
    for _ in range(400):
        trip, stations, rng, init = random_feasible_instance(r)
        vehicle = Vehicle(range_miles=rng, mpg=1.0)  # gallons == miles
        cands = [cand(i, m, p) for i, (m, p) in enumerate(stations)]

        plan = plan_fuel_stops_greedy(trip, cands, vehicle, initial_fuel_miles=init)

        expected = brute_force_cost(trip, stations, rng, init)
        assert plan.total_cost == pytest.approx(expected, abs=1e-6), (trip, stations, rng, init)
        check_invariants(plan, rng)


@pytest.mark.parametrize("penalty", [0.0, 0.5, 2.0, 10.0])
def test_dp_matches_exact_oracle_on_random_instances(penalty: float) -> None:
    r = random.Random(int(20260928 + penalty * 10))
    for _ in range(300):
        trip, stations, rng, init = random_feasible_instance(r)
        vehicle = Vehicle(range_miles=rng, mpg=1.0)
        cands = [cand(i, m, p) for i, (m, p) in enumerate(stations)]

        plan = plan_fuel_stops(trip, cands, vehicle, initial_fuel_miles=init, stop_penalty=penalty)

        expected = brute_force_cost(trip, stations, rng, init, penalty)
        objective = plan.total_cost + penalty * plan.stop_count
        assert objective == pytest.approx(expected, abs=1e-6), (trip, stations, rng, init, penalty)
        check_invariants(plan, rng)


def test_dp_with_zero_penalty_equals_greedy_cost() -> None:
    r = random.Random(7)
    for _ in range(300):
        trip, stations, rng, init = random_feasible_instance(r)
        vehicle = Vehicle(range_miles=rng, mpg=1.0)
        cands = [cand(i, m, p) for i, (m, p) in enumerate(stations)]

        dp = plan_fuel_stops(trip, cands, vehicle, initial_fuel_miles=init)
        greedy = plan_fuel_stops_greedy(trip, cands, vehicle, initial_fuel_miles=init)

        assert dp.total_cost == pytest.approx(greedy.total_cost, abs=1e-6)
        assert dp.stop_count <= greedy.stop_count  # DP breaks cost ties toward fewer stops


def test_dp_gap_errors_match_greedy_on_infeasible_instances() -> None:
    r = random.Random(99)
    seen = 0
    while seen < 50:
        rng = r.randint(3, 15)
        trip = r.randint(10, 60)
        miles = sorted(r.randint(0, trip) for _ in range(r.randint(1, 5)))
        cands = [cand(i, m, 3.0) for i, m in enumerate(miles)]
        vehicle = Vehicle(range_miles=rng, mpg=1.0)
        try:
            plan_fuel_stops_greedy(trip, cands, vehicle)
        except StationGapTooLarge as greedy_exc:
            with pytest.raises(StationGapTooLarge) as dp_exc:
                plan_fuel_stops(trip, cands, vehicle)
            assert (dp_exc.value.from_mile, dp_exc.value.to_mile) == (
                greedy_exc.from_mile,
                greedy_exc.to_mile,
            )
            seen += 1


# ---------------------------------------------------------------------------
# Negligible purchases (stations sharing a mile marker)
# ---------------------------------------------------------------------------


def test_stations_at_the_same_exit_do_not_produce_zero_gallon_stops() -> None:
    # Two stations 0.02 miles apart at the start; the cheaper one is second. Without merging,
    # the plan would "buy" 0.02 miles at the first to reach the second.
    plan = plan_fuel_stops(300, [cand(1, 0.00, 3.5), cand(2, 0.02, 3.0)], V, stop_penalty=0)

    assert ids(plan) == [2]
    assert plan.pre_trip is None or plan.pre_trip.gallons >= 0.01
    assert all(s.gallons >= 0.01 for s in plan.stops)
    assert plan.total_gallons == pytest.approx(30.0)
    assert plan.stops[0].fuel_on_arrival_gallons == pytest.approx(0.0)


def test_negligible_pre_trip_is_folded_into_first_stop() -> None:
    plan = plan_fuel_stops(300, [cand(1, 0.03, 3.0)], V)

    assert plan.pre_trip is None
    assert ids(plan) == [1]
    assert plan.stops[0].gallons == pytest.approx(30.0)
    assert plan.total_cost == pytest.approx(90.0)


def test_negligible_last_purchase_is_folded_backwards() -> None:
    # Fill at A (500 mi) then a 0.05 mile top-up at B to finish: B is dropped, A absorbs it.
    plan = plan_fuel_stops(500.05, [cand(1, 0, 3.0), cand(2, 500, 9.0)], V)

    assert ids(plan) == [1]
    assert plan.stops[0].gallons == pytest.approx(50.005)
    assert plan.total_gallons == pytest.approx(plan.gallons_consumed)
