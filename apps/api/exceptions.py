"""
API error handling.

Every error response has the shape ``{"detail": "...", "code": "..."}`` so clients
can rely on one contract. Domain and upstream errors are mapped here rather
than leaking as 500s.
"""

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

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


class PlanningError(APIException):
    """The route exists but no feasible fuel plan does (e.g. a gap wider than the range)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "No feasible fuel plan exists for this route."
    default_code = "planning_error"


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled error in %s", context.get("view"))
        return Response(
            {"detail": "Internal server error.", "code": "server_error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    data = response.data
    if isinstance(data, dict) and "detail" in data and "code" not in data:
        code = getattr(exc, "default_code", None) or getattr(exc, "get_codes", lambda: None)()
        data["code"] = code if isinstance(code, str) else "error"
    elif isinstance(data, dict) and "detail" not in data:
        # Serializer validation errors: {"field": ["msg"]} -> keep field errors, add summary.
        response.data = {"detail": "Invalid request.", "code": "invalid", "errors": data}
    return response
