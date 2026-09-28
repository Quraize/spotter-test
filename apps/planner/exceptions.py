from __future__ import annotations


class PlanningError(Exception):
    """A route exists but no feasible fuel plan does."""


class NoStationsOnRoute(PlanningError):
    def __init__(self, trip_miles: float, corridor_miles: float | None = None) -> None:
        self.trip_miles = trip_miles
        self.corridor_miles = corridor_miles
        where = f" within {corridor_miles:g} miles of the route" if corridor_miles else ""
        super().__init__(
            f"The price list has no fuel station{where} on this {trip_miles:.0f} mile trip, "
            "so no fuel plan can be made. Coverage is thin in some states (e.g. California)."
        )


class StationGapTooLarge(PlanningError):
    def __init__(self, from_mile: float, to_mile: float, range_miles: float) -> None:
        self.from_mile = from_mile
        self.to_mile = to_mile
        self.range_miles = range_miles
        if from_mile == 0:
            msg = (
                f"The first station from the price list along this route is {to_mile:.0f} miles "
                f"in, beyond the vehicle range of {range_miles:g} miles. The list has little or "
                "no coverage on this stretch (e.g. California)."
            )
        else:
            msg = (
                f"The price list has no station along the route between mile {from_mile:.0f} "
                f"and mile {to_mile:.0f}, a gap of {to_mile - from_mile:.0f} miles that exceeds "
                f"the vehicle range of {range_miles:g} miles."
            )
        super().__init__(msg)
