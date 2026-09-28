from django import get_version
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe. Deliberately touches nothing external."""

    throttle_classes = []

    @extend_schema(responses={200: {"type": "object"}}, tags=["ops"])
    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "django": get_version()})
