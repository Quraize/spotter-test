"""
Turn user input for a location into a Place, using as few external calls as possible.

Resolution ladder (first match wins):

    "41.88,-87.63"            coordinates                 0 external calls
    "Chicago, IL" / "Chicago, Illinois" / "Chicago IL"
                              local city table            0 external calls
    "Chicago"                 local table if exactly one state has that city,
                              otherwise the live geocoder ranks the options
    anything else             live geocoder (street addresses, ZIPs, landmarks)   1 call

Every result is checked against the USA bounding boxes. The resolved place is returned with
its ``source`` so the API can echo back exactly what was matched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.routing.base import GeocodeProvider, Place
from apps.routing.local_cities import LocalCityIndex
from apps.routing.usa import is_in_usa

_COORDS = re.compile(r"^\s*([-+]?\d{1,3}(?:\.\d+)?)\s*,\s*([-+]?\d{1,3}(?:\.\d+)?)\s*$")
_COUNTRY_SUFFIXES = ("usa", "us", "u.s.", "u.s.a.", "united states", "united states of america")
MAX_QUERY_LENGTH = 200


class LocationNotFound(Exception):
    """Input could not be resolved to a place inside the USA."""

    def __init__(self, query: str, reason: str) -> None:
        self.query = query
        self.reason = reason
        super().__init__(f"{query!r}: {reason}")


@dataclass(frozen=True, slots=True)
class LocationResolver:
    cities: LocalCityIndex
    geocoder: GeocodeProvider | None = None

    def resolve(self, raw: str) -> Place:
        query = " ".join((raw or "").split())
        if not query:
            raise ValueError("location must not be empty")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"location must be at most {MAX_QUERY_LENGTH} characters")

        if (place := self._from_coordinates(query)) is not None:
            return self._checked(query, place)

        stripped = self._strip_country(query)
        if (place := self._from_local(stripped)) is not None:
            return place

        return self._checked(query, self._from_geocoder(query))

    # -- rungs ---------------------------------------------------------------------------

    @staticmethod
    def _from_coordinates(query: str) -> Place | None:
        match = _COORDS.match(query)
        if not match:
            return None
        lat, lng = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise LocationNotFound(query, "coordinates out of range; expected 'lat,lng'")
        return Place(name=f"{lat:.5f}, {lng:.5f}", lat=lat, lng=lng, source="coords")

    def _from_local(self, query: str) -> Place | None:
        parts = [p.strip() for p in query.split(",") if p.strip()]
        if len(parts) == 2:
            hit = self.cities.lookup(parts[0], parts[1])
            return hit.as_place() if hit else None
        if len(parts) != 1:
            return None

        words = parts[0].split()
        if len(words) >= 2 and self.cities.state_code(words[-1]):
            hit = self.cities.lookup(" ".join(words[:-1]), words[-1])
            if hit:
                return hit.as_place()
        options = self.cities.candidates(parts[0])
        if len(options) == 1:
            return options[0].as_place()
        return None  # unknown, or ambiguous ("Springfield"): let the geocoder rank it

    def _from_geocoder(self, query: str) -> Place:
        if self.geocoder is None:
            raise LocationNotFound(query, "not a known 'City, ST' and no geocoder is configured")
        place = self.geocoder.geocode(query)
        if place is None:
            raise LocationNotFound(query, "no match from the geocoder; try 'City, ST' or 'lat,lng'")
        return place

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _strip_country(query: str) -> str:
        parts = [p.strip() for p in query.split(",")]
        while len(parts) > 1 and parts[-1].lower() in _COUNTRY_SUFFIXES:
            parts.pop()
        return ", ".join(parts)

    @staticmethod
    def _checked(query: str, place: Place) -> Place:
        if not is_in_usa(place.lat, place.lng):
            raise LocationNotFound(query, f"resolved to {place.name!r}, which is outside the USA")
        return place
