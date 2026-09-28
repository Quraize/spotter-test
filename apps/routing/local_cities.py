"""
Offline "City, ST" -> coordinates lookup backed by data/us_cities.csv, built by
`manage.py build_city_table` from GeoNames (CC BY 4.0) and kelvins/US-Cities-Database (MIT).
Rows carry the alternative source's coordinates and how far apart the sources are, so callers
can treat a disputed place (duplicate town names within a state) as uncertain.

Used for two things:
  * the one-time station geocoding (resolves ~99.8% of the fuel CSV without a network call)
  * resolving user input like "Chicago, IL" at request time with zero external calls
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.routing.base import Place

# 50 states + DC. Territories are excluded: no road route exists to them.
US_STATES: frozenset[str] = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

UNCERTAIN_MILES = 5.0


def normalize_city(city: str) -> str:
    return " ".join(city.strip().lower().split())


@dataclass(frozen=True, slots=True)
class CityRecord:
    city: str
    state: str
    lat: float
    lng: float
    source: str = "local"
    alt: tuple[float, float] | None = None  # the other source's (lat, lng), if any
    disagreement_miles: float | None = None

    @property
    def is_uncertain(self) -> bool:
        return self.disagreement_miles is not None and self.disagreement_miles > UNCERTAIN_MILES

    def as_place(self) -> Place:
        return Place(
            name=f"{self.city}, {self.state}, USA",
            lat=self.lat,
            lng=self.lng,
            source="local",
            state=self.state,
        )


class LocalCityIndex:
    """In-memory index keyed by (normalized city, state). Loads once per process."""

    def __init__(self, path: Path) -> None:
        self._by_key: dict[tuple[str, str], CityRecord] = {}
        self._by_city: dict[str, list[CityRecord]] = {}
        self._state_by_name: dict[str, str] = {}
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                state = row["STATE_CODE"].strip().upper()
                if state not in US_STATES:
                    continue
                self._state_by_name.setdefault(normalize_city(row["STATE_NAME"]), state)
                alt_lat, alt_lng = row.get("ALT_LATITUDE", ""), row.get("ALT_LONGITUDE", "")
                disagreement = row.get("DISAGREEMENT_MILES", "")
                record = CityRecord(
                    city=row["CITY"].strip(),
                    state=state,
                    lat=float(row["LATITUDE"]),
                    lng=float(row["LONGITUDE"]),
                    source=row.get("SOURCE", "") or "local",
                    alt=(float(alt_lat), float(alt_lng)) if alt_lat and alt_lng else None,
                    disagreement_miles=float(disagreement) if disagreement else None,
                )
                key = (normalize_city(record.city), state)
                # First occurrence wins; duplicates in the source are the same place repeated.
                if key not in self._by_key:
                    self._by_key[key] = record
                    self._by_city.setdefault(key[0], []).append(record)

    def __len__(self) -> int:
        return len(self._by_key)

    def state_code(self, text: str) -> str | None:
        """'IL', 'il', or 'Illinois' -> 'IL'; None if it is not a US state."""
        token = text.strip()
        if len(token) == 2 and token.upper() in US_STATES:
            return token.upper()
        return self._state_by_name.get(normalize_city(token))

    def lookup(self, city: str, state: str) -> CityRecord | None:
        code = self.state_code(state)
        if code is None:
            return None
        return self._by_key.get((normalize_city(city), code))

    def candidates(self, city: str) -> list[CityRecord]:
        """Every state that has a city with this name. Used to detect ambiguous input."""
        return list(self._by_city.get(normalize_city(city), ()))


@lru_cache(maxsize=1)
def get_city_index() -> LocalCityIndex:
    return LocalCityIndex(Path(settings.DATA_DIR) / "us_cities.csv")
