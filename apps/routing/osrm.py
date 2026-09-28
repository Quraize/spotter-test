"""
OSRM routing. Default host is the public demo server, which needs no key but is explicitly
non-commercial, unguaranteed and limited to ~1 request/second, so it is the fallback, not
the primary. Point OSRM_BASE_URL at a self-hosted instance for production.

GET /route/v1/driving/{lng},{lat};{lng},{lat}?overview=full&geometries=geojson
"""

from __future__ import annotations

from django.conf import settings

from apps.routing.base import METERS_PER_MILE, Place, ProviderError, Route
from apps.routing.http import make_client, request_json


class OSRMRouter:
    name = "osrm"

    def __init__(self, base_url: str | None = None, profile: str = "driving") -> None:
        self.profile = profile
        self._client = make_client(base_url or settings.OSRM_BASE_URL)

    def route(self, start: Place, finish: Place) -> Route:
        path = f"/route/v1/{self.profile}/{start.lng},{start.lat};{finish.lng},{finish.lat}"
        params = {"overview": "full", "geometries": "geojson", "steps": "false"}
        data = request_json(self._client, "GET", path, provider=self.name, params=params)
        if not isinstance(data, dict) or data.get("code") != "Ok":
            code = data.get("code") if isinstance(data, dict) else "?"
            raise ProviderError(
                f"osrm: {code} {data.get('message', '') if isinstance(data, dict) else ''}"
            )
        try:
            best = data["routes"][0]
            coords = best["geometry"]["coordinates"]
            distance_m = float(best["distance"])
            duration_s = float(best["duration"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("osrm: unexpected route payload") from exc
        if len(coords) < 2:
            raise ProviderError("osrm: route geometry has fewer than two points")
        return Route(
            distance_miles=distance_m / METERS_PER_MILE,
            duration_minutes=duration_s / 60.0,
            coordinates=[(float(c[0]), float(c[1])) for c in coords],
            provider=self.name,
        )

    def close(self) -> None:
        self._client.close()
