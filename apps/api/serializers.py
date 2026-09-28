"""
Request validation and response shape. The response serializers are the OpenAPI contract.
"""

from __future__ import annotations

import shapely
from django.conf import settings
from rest_framework import serializers

from apps.api.services import PlanRequest, PlanResult

MAX_LOCATION_LENGTH = 200
# Response geometry is simplified to ~10 m for transport; planning always used the full line.
GEOMETRY_TOLERANCE_DEGREES = 0.0001


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=MAX_LOCATION_LENGTH,
        help_text="Start location in the USA: 'City, ST', a street address, or 'lat,lng'.",
    )
    finish = serializers.CharField(
        max_length=MAX_LOCATION_LENGTH,
        help_text="Finish location in the USA, same formats as start.",
    )
    initial_fuel_miles = serializers.FloatField(
        required=False,
        default=0.0,
        min_value=0.0,
        help_text=(
            "Miles of range in the tank at departure (0 = empty, 500 = full). With 0 the whole "
            "trip's fuel is paid for; any shortfall to the first station is a pre-trip purchase."
        ),
    )
    stop_penalty = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=1000.0,
        help_text=(
            "Dollar cost assigned to each stop (driver time). 0 gives the pure cost minimum, "
            "which stops at every marginally cheaper station. The reported total is fuel only."
        ),
    )
    corridor_miles = serializers.FloatField(
        required=False,
        min_value=1.0,
        max_value=25.0,
        help_text="How far off the route a station may be to count as 'along the route'.",
    )
    include_geometry = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Set false to omit the route LineString (much smaller response).",
    )

    def validate_initial_fuel_miles(self, value: float) -> float:
        if value > settings.VEHICLE_RANGE_MILES:
            raise serializers.ValidationError(
                f"cannot exceed the vehicle range of {settings.VEHICLE_RANGE_MILES:g} miles."
            )
        return value

    def to_plan_request(self) -> PlanRequest:
        d = self.validated_data
        return PlanRequest(
            start=d["start"].strip(),
            finish=d["finish"].strip(),
            initial_fuel_miles=d["initial_fuel_miles"],
            stop_penalty=d.get("stop_penalty", settings.STOP_PENALTY_USD),
            corridor_miles=d.get("corridor_miles", settings.CORRIDOR_MILES),
            include_geometry=d["include_geometry"],
        )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class PlaceSerializer(serializers.Serializer):
    query = serializers.CharField()
    name = serializers.CharField(source="place.name")
    lat = serializers.FloatField(source="place.lat")
    lng = serializers.FloatField(source="place.lng")
    source = serializers.CharField(
        source="place.source", help_text="coords | local | ors | nominatim"
    )


class GeometrySerializer(serializers.Serializer):
    type = serializers.CharField()
    coordinates = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        help_text="GeoJSON LineString, [lng, lat] pairs.",
    )


class RouteSerializer(serializers.Serializer):
    provider = serializers.CharField()
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.FloatField()
    geometry = GeometrySerializer(allow_null=True)


class VehicleSerializer(serializers.Serializer):
    range_miles = serializers.FloatField()
    mpg = serializers.FloatField()
    tank_gallons = serializers.FloatField()


class StationSerializer(serializers.Serializer):
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()


class FuelStopSerializer(serializers.Serializer):
    order = serializers.IntegerField()
    station = StationSerializer()
    mile_marker = serializers.FloatField(source="stop.mile_marker")
    detour_miles = serializers.FloatField(source="stop.candidate.detour_miles")
    price_per_gallon = serializers.FloatField(source="stop.price")
    gallons = serializers.FloatField(source="stop.gallons")
    cost = serializers.FloatField(source="stop.cost")
    fuel_on_arrival_gallons = serializers.FloatField(source="stop.fuel_on_arrival_gallons")
    fuel_on_departure_gallons = serializers.FloatField(source="stop.fuel_on_departure_gallons")


class PreTripPurchaseSerializer(serializers.Serializer):
    station = StationSerializer()
    price_per_gallon = serializers.FloatField(source="stop.price")
    gallons = serializers.FloatField(source="stop.gallons")
    cost = serializers.FloatField(source="stop.cost")
    note = serializers.SerializerMethodField()

    def get_note(self, obj) -> str:
        return (
            "Fuel needed to reach the first station from the start, bought before departure "
            "at that station's price."
        )


class SummarySerializer(serializers.Serializer):
    total_fuel_cost = serializers.FloatField()
    total_gallons_purchased = serializers.FloatField()
    gallons_consumed = serializers.FloatField()
    fuel_at_finish_gallons = serializers.FloatField()
    stop_count = serializers.IntegerField()
    candidate_stations = serializers.IntegerField(
        help_text="Stations found within the corridor and considered by the optimiser."
    )


class AssumptionsSerializer(serializers.Serializer):
    initial_fuel_miles = serializers.FloatField()
    stop_penalty_usd = serializers.FloatField()
    corridor_miles = serializers.FloatField()
    optimizer = serializers.CharField()
    prices = serializers.CharField()


class ExternalCallsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    providers = serializers.ListField(child=serializers.CharField())


class RoutePlanResponseSerializer(serializers.Serializer):
    start = PlaceSerializer()
    finish = PlaceSerializer()
    route = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    fuel_stops = FuelStopSerializer(many=True, source="stops")
    pre_trip_purchase = PreTripPurchaseSerializer(source="pre_trip", allow_null=True)
    summary = serializers.SerializerMethodField()
    assumptions = serializers.SerializerMethodField()
    external_calls = serializers.SerializerMethodField()
    timings_ms = serializers.DictField(child=serializers.FloatField())

    def get_route(self, r: PlanResult) -> dict:
        geometry = None
        if r.request.include_geometry:
            line = shapely.LineString(r.route.coordinates).simplify(
                GEOMETRY_TOLERANCE_DEGREES, preserve_topology=False
            )
            geometry = {
                "type": "LineString",
                "coordinates": [[round(lng, 5), round(lat, 5)] for lng, lat in line.coords],
            }
        return RouteSerializer(
            {
                "provider": r.route.provider,
                "distance_miles": round(r.route.distance_miles, 1),
                "duration_minutes": round(r.route.duration_minutes),
                "geometry": geometry,
            }
        ).data

    def get_vehicle(self, r: PlanResult) -> dict:
        return VehicleSerializer(
            {
                "range_miles": r.vehicle.range_miles,
                "mpg": r.vehicle.mpg,
                "tank_gallons": r.vehicle.tank_gallons,
            }
        ).data

    def get_summary(self, r: PlanResult) -> dict:
        return SummarySerializer(
            {
                "total_fuel_cost": round(r.plan.total_cost, 2),
                "total_gallons_purchased": round(r.plan.total_gallons, 2),
                "gallons_consumed": round(r.plan.gallons_consumed, 2),
                "fuel_at_finish_gallons": round(r.plan.fuel_at_finish_gallons, 2),
                "stop_count": r.plan.stop_count,
                "candidate_stations": r.candidate_count,
            }
        ).data

    def get_assumptions(self, r: PlanResult) -> dict:
        return AssumptionsSerializer(
            {
                "initial_fuel_miles": r.request.initial_fuel_miles,
                "stop_penalty_usd": r.request.stop_penalty,
                "corridor_miles": r.request.corridor_miles,
                "optimizer": "exact DP minimising fuel cost + stop_penalty per stop",
                "prices": "OPIS retail diesel prices from the supplied file",
            }
        ).data

    def get_external_calls(self, r: PlanResult) -> dict:
        return ExternalCallsSerializer(
            {"count": len(r.external_calls), "providers": r.external_calls}
        ).data

    def to_representation(self, instance: PlanResult) -> dict:
        data = super().to_representation(instance)
        for stop in data["fuel_stops"]:
            _round_stop(stop)
        if data["pre_trip_purchase"]:
            _round_stop(data["pre_trip_purchase"])
        return data


def _round_stop(stop: dict) -> None:
    for key, digits in (
        ("mile_marker", 1),
        ("detour_miles", 1),
        ("price_per_gallon", 3),
        ("gallons", 2),
        ("cost", 2),
        ("fuel_on_arrival_gallons", 2),
        ("fuel_on_departure_gallons", 2),
    ):
        if key in stop:
            stop[key] = round(stop[key], digits)
    stop["station"]["lat"] = round(stop["station"]["lat"], 5)
    stop["station"]["lng"] = round(stop["station"]["lng"], 5)


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()
    errors = serializers.DictField(required=False)
