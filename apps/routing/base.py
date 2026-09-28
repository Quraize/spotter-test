"""
Provider-agnostic types and interfaces for routing and geocoding.

Everything outside ``apps.routing`` talks only to these types. Concrete
providers (ORS, OSRM, Nominatim) normalise their responses into them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

METERS_PER_MILE = 1609.344


class ProviderError(Exception):
    """A provider failed, returned an error status, or returned an unusable payload."""


class ProviderTimeout(ProviderError):
    """A provider did not answer within the configured timeout."""


@dataclass(frozen=True, slots=True)
class Place:
    """A resolved location. ``source`` records how it was resolved (coords/local/ors/nominatim)."""

    name: str
    lat: float
    lng: float
    source: str
    state: str | None = None  # two-letter code when known

    @property
    def lnglat(self) -> tuple[float, float]:
        return (self.lng, self.lat)


@dataclass(frozen=True, slots=True)
class Route:
    """A driving route. ``coordinates`` are GeoJSON order: (lng, lat)."""

    distance_miles: float
    duration_minutes: float
    coordinates: list[tuple[float, float]]
    provider: str

    @property
    def geojson(self) -> dict:
        return {"type": "LineString", "coordinates": [list(c) for c in self.coordinates]}


class GeocodeProvider(Protocol):
    name: str

    def geocode(self, query: str) -> Place | None:
        """Resolve free text to a Place inside the USA, or None if nothing matched."""
        ...


class RouteProvider(Protocol):
    name: str

    def route(self, start: Place, finish: Place) -> Route:
        """Return a driving route between two places. Raises ProviderError on failure."""
        ...
