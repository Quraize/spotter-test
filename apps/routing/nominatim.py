"""
Nominatim (OpenStreetMap) geocoder.

Used as the fallback geocoder and for the one-time offline station geocoding.
Usage policy: max 1 request/second, identifying User-Agent, no heavy use.
https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

from django.conf import settings

from apps.routing.base import Place
from apps.routing.http import make_client, request_json


class NominatimGeocoder:
    name = "nominatim"

    def __init__(self, base_url: str | None = None) -> None:
        self._client = make_client(base_url or settings.NOMINATIM_BASE_URL)

    def geocode(self, query: str) -> Place | None:
        params = {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us"}
        results = request_json(self._client, "GET", "/search", provider=self.name, params=params)
        return self._first(results)

    def geocode_city(self, city: str, state: str) -> Place | None:
        """Structured lookup, more precise than free text for 'City, ST' pairs."""
        params = {
            "city": city,
            "state": state,
            "country": "United States",
            "format": "jsonv2",
            "limit": 1,
        }
        results = request_json(self._client, "GET", "/search", provider=self.name, params=params)
        return self._first(results)

    def _first(self, results: object) -> Place | None:
        if not isinstance(results, list) or not results:
            return None
        hit = results[0]
        try:
            return Place(
                name=str(hit.get("display_name", "")),
                lat=float(hit["lat"]),
                lng=float(hit["lon"]),
                source=self.name,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def close(self) -> None:
        self._client.close()
