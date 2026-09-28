from __future__ import annotations

from django import get_version
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.exceptions import translate
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
along it for a vehicle with a 500 mile range and 10 mpg, and the total fuel spend. The
response includes `map_url`, an HTML page rendering the same plan on a map.

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
        return self._plan(request, request.query_params)

    @extend_schema(
        summary="Plan a route with optimal fuel stops (JSON body)",
        description=_DESCRIPTION,
        request=RoutePlanRequestSerializer,
        responses={200: RoutePlanResponseSerializer, **_ERRORS},
        examples=[_EXAMPLE],
        tags=["planning"],
    )
    def post(self, request: Request) -> Response:
        return self._plan(request, request.data)

    def _plan(self, request: Request, data) -> Response:
        serializer = RoutePlanRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = plan_route(serializer.to_plan_request())
        payload = RoutePlanResponseSerializer(result, context={"request": request}).data
        return Response(payload, status=status.HTTP_200_OK)


def plan_query(req) -> dict[str, str]:
    """The query string that reproduces a PlanRequest exactly (used for map/API links)."""
    return {
        "start": req.start,
        "finish": req.finish,
        "initial_fuel_miles": f"{req.initial_fuel_miles:g}",
        "stop_penalty": f"{req.stop_penalty:g}",
        "corridor_miles": f"{req.corridor_miles:g}",
    }


class RoutePlanMapView(View):
    """
    Human-facing HTML page: the same plan as the API, drawn with Leaflet.

    Rendered server-side from the cached route, so opening it after an API call makes no
    upstream request. Errors use the same detail/code contract, shown in the page.
    """

    template_name = "api/map.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        form = {
            "start": request.GET.get("start", ""),
            "finish": request.GET.get("finish", ""),
            "initial_fuel_miles": request.GET.get("initial_fuel_miles", "0"),
            "stop_penalty": request.GET.get("stop_penalty", f"{settings.STOP_PENALTY_USD:g}"),
            "corridor_miles": request.GET.get("corridor_miles", f"{settings.CORRIDOR_MILES:g}"),
        }
        if not request.GET:
            return render(request, self.template_name, {"form": form})

        serializer = RoutePlanRequestSerializer(data=request.GET)
        if not serializer.is_valid():
            error = {"detail": "Invalid request.", "code": "invalid", "errors": serializer.errors}
            return render(request, self.template_name, {"form": form, "error": error}, status=400)

        plan_request = serializer.to_plan_request()
        try:
            result = plan_route(plan_request)
        except Exception as exc:
            api_exc = translate(exc)
            if api_exc is None:
                raise
            error = {"detail": str(api_exc.detail), "code": api_exc.default_code}
            return render(
                request,
                self.template_name,
                {"form": form, "error": error},
                status=api_exc.status_code,
            )

        plan = RoutePlanResponseSerializer(result, context={"request": request}).data
        api_url = reverse("api:route-plan") + "?" + urlencode(plan_query(plan_request))
        return render(request, self.template_name, {"form": form, "plan": plan, "api_url": api_url})
