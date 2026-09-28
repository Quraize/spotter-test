from django import get_version
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.serializers import (
    ErrorSerializer,
    RoutePlanRequestSerializer,
    RoutePlanResponseSerializer,
)
from apps.api.services import plan_route


class HealthView(APIView):
    """Liveness probe. Deliberately touches nothing external."""

    throttle_classes = []

    @extend_schema(responses={200: {"type": "object"}}, tags=["ops"])
    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "django": get_version()})


_ERRORS = {
    400: ErrorSerializer,
    422: ErrorSerializer,
    429: ErrorSerializer,
    502: ErrorSerializer,
    503: ErrorSerializer,
    504: ErrorSerializer,
}

_DESCRIPTION = """
Plan a US road trip and choose where to buy fuel.

Given a start and finish, returns the driving route (GeoJSON), the cost-optimal fuel stops
along it for a vehicle with a 500 mile range and 10 mpg, and the total fuel spend.

**Inputs** accept `City, ST`, a street address, or `lat,lng`. Known cities and coordinates
resolve locally; anything else costs one geocoding call per endpoint. Routing is one call,
cached by endpoints, so repeated requests make no upstream calls at all. The response reports
`external_calls` so you can see this.

**Optimality.** The optimiser is an exact dynamic program over the stations in the corridor
that minimises fuel cost plus a per-stop penalty (driver time). With `stop_penalty=0` it
returns the pure cost minimum.
"""

_EXAMPLE = OpenApiExample(
    "Chicago to Dallas",
    value={"start": "Chicago, IL", "finish": "Dallas, TX"},
    request_only=True,
)


class RoutePlanView(APIView):
    """GET with query parameters or POST with a JSON body; both take the same fields."""

    @extend_schema(
        summary="Plan a route with optimal fuel stops",
        description=_DESCRIPTION,
        parameters=[RoutePlanRequestSerializer],
        responses={200: RoutePlanResponseSerializer, **_ERRORS},
        tags=["planning"],
    )
    def get(self, request: Request) -> Response:
        return self._plan(request.query_params)

    @extend_schema(
        summary="Plan a route with optimal fuel stops (JSON body)",
        description=_DESCRIPTION,
        request=RoutePlanRequestSerializer,
        responses={200: RoutePlanResponseSerializer, **_ERRORS},
        examples=[_EXAMPLE],
        tags=["planning"],
    )
    def post(self, request: Request) -> Response:
        return self._plan(request.data)

    def _plan(self, data) -> Response:
        serializer = RoutePlanRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = plan_route(serializer.to_plan_request())
        return Response(RoutePlanResponseSerializer(result).data, status=status.HTTP_200_OK)
