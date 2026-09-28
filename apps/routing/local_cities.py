"""
Offline "City, ST" -> coordinates lookup backed by data/us_cities.csv
(MIT licensed, from kelvins/US-Cities-Database).

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

US_STATES: frozenset[str] = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    ]
)

STATE_NAMES: dict[str, str] = {}  # filled lazily from the CSV (e.g. "IL" -> "Illinois")


def normalize_city(city: str) -> str:
    return " ".join(city.strip().lower().split())


@dataclass(frozen=True, slots=True)
class CityRecord:
    city: str
    state: str
    lat: float
    lng: float

    def as_place(self) -> Place:
        return Place(
            name=f"{self.city}, {self.state}, USA", lat=self.lat, lng=self.lng, source="local"
        )


class LocalCityIndex:
    """In-memory index keyed by (normalized city, state). Loads once per process."""

    def __init__(self, path: Path) -> None:
        self._by_key: dict[tuple[str, str], CityRecord] = {}
        self._by_city: dict[str, list[CityRecord]] = {}
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                state = row["STATE_CODE"].strip().upper()
                if state not in US_STATES:
                    continue
                STATE_NAMES.setdefault(state, row["STATE_NAME"].strip())
                record = CityRecord(
                    city=row["CITY"].strip(),
                    state=state,
                    lat=float(row["LATITUDE"]),
                    lng=float(row["LONGITUDE"]),
                )
                key = (normalize_city(record.city), state)
                # First occurrence wins; duplicates in the source are the same place repeated.
                self._by_key.setdefault(key, record)
                self._by_city.setdefault(key[0], []).append(record)

    def __len__(self) -> int:
        return len(self._by_key)

    def lookup(self, city: str, state: str) -> CityRecord | None:
        return self._by_key.get((normalize_city(city), state.strip().upper()))

    def candidates(self, city: str) -> list[CityRecord]:
        """All states that have a city with this name. Used to detect ambiguous input."""
        return list(self._by_city.get(normalize_city(city), ()))


@lru_cache(maxsize=1)
def get_city_index() -> LocalCityIndex:
    return LocalCityIndex(Path(settings.DATA_DIR) / "us_cities.csv")
