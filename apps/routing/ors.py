"""
OpenRouteService: directions (truck profile) and geocoding on one free API key.

Directions: POST /v2/directions/{profile}/geojson  -> GeoJSON FeatureCollection whose first
feature carries the LineString and ``properties.summary.{distance, duration}`` in m and s.
Geocoding: GET /geocode/search (Pelias) -> GeoJSON points with ``properties.label``.

Free tier at the time of writing: 2,000 directions and 1,000 geocode requests per day.
"""

from __future__ import annotations

from django.conf import settings

from apps.routing.base import METERS_PER_MILE, Place, ProviderError, Route
from apps.routing.http import make_client, request_json

# ORS snaps endpoints to the road network within 350 m by default and refuses beyond that,
# which rejects islands (Statue of Liberty) and area centroids (a ZIP or a county). Allow a
# generous snap; the response still reports the road-snapped geometry.
SNAP_RADIUS_METERS = 25_000

# Geocoder hits that describe an area rather than a place. A fuzzy match that falls back to
# a whole state or country ("Chicgo, IL" -> "Illinois") is not somewhere you can drive from.
_AREA_LAYERS = frozenset({"country", "dependency", "macroregion", "region", "macrocounty"})


class ORSClient:
    name = "ors"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.ORS_API_KEY
        self.profile = profile or settings.ORS_PROFILE
        if not self.api_key:
            raise ProviderError("ors: ORS_API_KEY is not configured")
        self._client = make_client(
            base_url or settings.ORS_BASE_URL,
            headers={
                "Authorization": self.api_key,
                # The /geojson directions endpoint answers 406 to a bare application/json.
                "Accept": "application/json, application/geo+json;q=0.9, */*;q=0.8",
            },
        )

    # -- RouteProvider -------------------------------------------------------------------

    def route(self, start: Place, finish: Place) -> Route:
        body = {
            "coordinates": [list(start.lnglat), list(finish.lnglat)],
            "instructions": False,  # we only need geometry and totals; halves the payload
            "units": "m",
            "radiuses": [SNAP_RADIUS_METERS, SNAP_RADIUS_METERS],
        }
        data = request_json(
            self._client,
            "POST",
            f"/v2/directions/{self.profile}/geojson",
            provider=self.name,
            json=body,
        )
        try:
            feature = data["features"][0]
            summary = feature["properties"]["summary"]
            coords = feature["geometry"]["coordinates"]
            distance_m = float(summary["distance"])
            duration_s = float(summary["duration"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("ors: unexpected directions payload") from exc
        if len(coords) < 2:
            raise ProviderError("ors: route geometry has fewer than two points")
        return Route(
            distance_miles=distance_m / METERS_PER_MILE,
            duration_minutes=duration_s / 60.0,
            coordinates=[(float(c[0]), float(c[1])) for c in coords],
            provider=self.name,
        )

    # -- GeocodeProvider -----------------------------------------------------------------

    def geocode(self, query: str) -> Place | None:
        params = {"text": query, "boundary.country": "USA", "size": 1}
        data = request_json(
            self._client, "GET", "/geocode/search", provider=self.name, params=params
        )
        features = data.get("features") if isinstance(data, dict) else None
        if not features:
            return None
        hit = features[0]
        try:
            props = hit.get("properties", {})
            if props.get("country_a", "USA") != "USA":
                return None
            if props.get("layer") in _AREA_LAYERS:
                return None
            lng, lat = hit["geometry"]["coordinates"][:2]
            region = str(props.get("region_a") or "").upper()
            return Place(
                name=str(props.get("label") or query),
                lat=float(lat),
                lng=float(lng),
                source=self.name,
                state=region if len(region) == 2 else None,
            )
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def close(self) -> None:
        self._client.close()
