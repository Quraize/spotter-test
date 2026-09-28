"""
API error handling.

Every error response has the shape ``{"detail": "...", "code": "..."}`` so clients can rely
on one contract. Domain and upstream errors are translated here rather than leaking as 500s.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.planner.exceptions import PlanningError as DomainPlanningError
from apps.routing.base import ProviderError, ProviderTimeout
from apps.routing.http import ProviderClientError
from apps.routing.resolver import LocationNotFound
from apps.stations.index import StationDataMissing

logger = logging.getLogger(__name__)


class UpstreamError(APIException):
    """An external routing/geocoding provider failed or returned garbage."""

    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "An external service failed while planning the route."
    default_code = "upstream_error"


class UpstreamTimeout(UpstreamError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    default_detail = "An external service timed out while planning the route."
    default_code = "upstream_timeout"


class UnresolvableLocation(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "A location could not be resolved to a place inside the USA."
    default_code = "unresolvable_location"


class Unroutable(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "No road route exists between these locations."
    default_code = "unroutable"


class PlanningError(APIException):
    """The route exists but no feasible fuel plan does (e.g. a gap wider than the range)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "No feasible fuel plan exists for this route."
    default_code = "planning_error"


class ServiceNotReady(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Station data is not loaded."
    default_code = "service_not_ready"


def translate(exc: Exception) -> APIException | None:
    """Domain/provider exceptions -> API exceptions. None if not ours."""
    if isinstance(exc, LocationNotFound):
        return UnresolvableLocation(detail=f"Could not resolve {exc.query!r}: {exc.reason}.")
    if isinstance(exc, DomainPlanningError):
        return PlanningError(detail=str(exc))
    if isinstance(exc, ProviderTimeout):
        return UpstreamTimeout(detail=str(exc))
    if isinstance(exc, ProviderClientError) and exc.is_bad_input:
        return Unroutable(detail=f"Routing provider rejected the locations: {exc}")
    if isinstance(exc, ProviderError):
        return UpstreamError(detail=str(exc))
    if isinstance(exc, StationDataMissing):
        return ServiceNotReady(detail=str(exc))
    if isinstance(exc, ValueError):  # resolver/planner argument validation
        return UnresolvableLocation(detail=str(exc)) if "location" in str(exc) else None
    return None


def exception_handler(exc, context):
    translated = translate(exc)
    if translated is not None:
        logger.info("request failed: %s", translated.detail)
        exc = translated

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled error in %s", context.get("view"))
        return Response(
            {"detail": "Internal server error.", "code": "server_error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    data = response.data
    if isinstance(data, dict) and "detail" in data:
        if "code" not in data:
            code = getattr(exc, "default_code", None)
            codes = exc.get_codes() if hasattr(exc, "get_codes") else None
            data["code"] = codes if isinstance(codes, str) else (code or "error")
    elif isinstance(data, dict):
        # Serializer validation errors: {"field": ["msg"]} -> keep field errors, add summary.
        response.data = {"detail": "Invalid request.", "code": "invalid", "errors": data}
    return response
