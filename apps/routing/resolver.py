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
from dataclasses import replace as dc_replace

from apps.routing.base import GeocodeProvider, Place
from apps.routing.local_cities import US_STATES, LocalCityIndex
from apps.routing.usa import is_in_usa

_COORDS = re.compile(r"^\s*([-+]?\d{1,3}(?:\.\d+)?)\s*,\s*([-+]?\d{1,3}(?:\.\d+)?)\s*$")
# Common nicknames. Without these a geocoder returns whatever venue matches the token best
# ("Philly" -> a restaurant in San Francisco).
_ALIASES = {
    "nyc": "New York, NY",
    "new york city": "New York, NY",
    "la": "Los Angeles, CA",
    "l.a.": "Los Angeles, CA",
    "sf": "San Francisco, CA",
    "philly": "Philadelphia, PA",
    "dc": "Washington, DC",
    "d.c.": "Washington, DC",
    "washington d.c.": "Washington, DC",
    "washington, d.c.": "Washington, DC",
    "vegas": "Las Vegas, NV",
    "nola": "New Orleans, LA",
    "atl": "Atlanta, GA",
    "chi-town": "Chicago, IL",
    "the big apple": "New York, NY",
    "motor city": "Detroit, MI",
}
_COUNTRY_SUFFIXES = ("usa", "us", "u.s.", "u.s.a.", "united states", "united states of america")
MAX_QUERY_LENGTH = 200
_STATE_IN_NAME = re.compile(r",\s*([A-Z]{2}),\s*(?:USA|United States)\s*$")


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

    def resolve(self, raw: str, state: str | None = None) -> Place:
        """
        Resolve free text to a Place. ``state`` (two-letter code) is an optional hint: it is
        appended to the text unless the text is coordinates or already names that state, so
        "Springfield" + "IL" resolves like "Springfield, IL".
        """
        query = " ".join((raw or "").split())
        if not query:
            raise ValueError("location must not be empty")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"location must be at most {MAX_QUERY_LENGTH} characters")

        if (place := self._from_coordinates(query)) is not None:
            return self._checked(query, place)

        base = self._strip_country(query)
        alias = _ALIASES.get(base.lower()) or _ALIASES.get(base.rstrip(".;,!").lower())
        stripped = alias or base.rstrip(".;,!")
        if state:
            stripped = self._with_state(stripped, state)
        if (place := self._from_local(stripped)) is not None:
            return place

        # A recognised nickname is sent to the geocoder expanded; anything else goes verbatim
        # (street addresses and landmarks geocode best untouched), plus the state hint.
        text = alias or query
        if state:
            text = self._with_state(text, state)
        return self._checked(query, self._from_geocoder(text))

    def _with_state(self, text: str, state: str) -> str:
        code = state.strip().upper()
        if code not in US_STATES:
            raise ValueError(f"unknown state code {state!r}")
        parts = [p.strip() for p in text.split(",") if p.strip()]
        last_words = parts[-1].split() if parts else []
        already = parts and (
            self.cities.state_code(parts[-1]) == code
            or (len(last_words) >= 2 and self.cities.state_code(last_words[-1]) == code)
        )
        return text if already else f"{text}, {code}"

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

        if self.cities.state_code(parts[0]):
            return None  # a bare state ("Ohio", "TX"): let the geocoder place it, not a
            # same-named village in the city table
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
        if place.state is None and (m := _STATE_IN_NAME.search(place.name)):
            place = dc_replace(place, state=m.group(1))
        return place
