from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Vehicle:
    range_miles: float = 500.0
    mpg: float = 10.0

    def __post_init__(self) -> None:
        if self.range_miles <= 0 or self.mpg <= 0:
            raise ValueError("range_miles and mpg must be positive")

    @property
    def tank_gallons(self) -> float:
        return self.range_miles / self.mpg

    def gallons(self, miles: float) -> float:
        return miles / self.mpg


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fuel station projected onto the route."""

    station_id: int
    price: float  # $/gallon
    mile_marker: float  # distance along the route from the start, miles
    detour_miles: float  # straight-line distance from the route
    lat: float
    lng: float


@dataclass(frozen=True, slots=True)
class FuelStop:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float
    fuel_on_departure_gallons: float

    @property
    def mile_marker(self) -> float:
        return self.candidate.mile_marker

    @property
    def price(self) -> float:
        return self.candidate.price


@dataclass(frozen=True, slots=True)
class FuelPlan:
    trip_miles: float
    vehicle: Vehicle
    initial_fuel_gallons: float
    # Fuel bought before departure (at the first station's price) when the initial fuel
    # cannot reach the first station. None when it can.
    pre_trip: FuelStop | None
    stops: tuple[FuelStop, ...]
    total_gallons: float
    total_cost: float
    fuel_at_finish_gallons: float

    @property
    def gallons_consumed(self) -> float:
        return self.vehicle.gallons(self.trip_miles)

    @property
    def stop_count(self) -> int:
        return len(self.stops)
