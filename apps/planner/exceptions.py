from __future__ import annotations


class PlanningError(Exception):
    """A route exists but no feasible fuel plan does."""


class NoStationsOnRoute(PlanningError):
    def __init__(self, trip_miles: float, corridor_miles: float | None = None) -> None:
        self.trip_miles = trip_miles
        self.corridor_miles = corridor_miles
        where = f" within {corridor_miles:g} miles of the route" if corridor_miles else ""
        super().__init__(f"No fuel stations found{where} on a {trip_miles:.0f} mile trip.")


class StationGapTooLarge(PlanningError):
    def __init__(self, from_mile: float, to_mile: float, range_miles: float) -> None:
        self.from_mile = from_mile
        self.to_mile = to_mile
        self.range_miles = range_miles
        if from_mile == 0:
            msg = (
                f"The first usable fuel station is {to_mile:.0f} miles into the route, beyond "
                f"the vehicle range of {range_miles:g} miles."
            )
        else:
            msg = (
                f"Gap of {to_mile - from_mile:.0f} miles between usable stations at mile "
                f"{from_mile:.0f} and mile {to_mile:.0f} exceeds the vehicle range of "
                f"{range_miles:g} miles."
            )
        super().__init__(msg)
