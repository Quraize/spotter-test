"""
The geocoded places file: one row per (city, state) pair from the fuel CSV.

This is the committed output of ``geocode_stations`` and the input to ``import_stations``.
Keeping it as a small CSV (rather than coordinates baked into a fixture) makes the geocoding
step reviewable and re-runnable.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from apps.routing.local_cities import normalize_city

COLUMNS = ("city", "state", "latitude", "longitude", "source", "display_name")

PlaceKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class GeocodedPlace:
    city: str
    state: str
    latitude: float
    longitude: float
    source: str
    display_name: str

    @property
    def key(self) -> PlaceKey:
        return place_key(self.city, self.state)


def place_key(city: str, state: str) -> PlaceKey:
    return (normalize_city(city), state.strip().upper())


def read_places(path: Path) -> dict[PlaceKey, GeocodedPlace]:
    if not path.exists():
        return {}
    places: dict[PlaceKey, GeocodedPlace] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            place = GeocodedPlace(
                city=row["city"],
                state=row["state"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                source=row["source"],
                display_name=row.get("display_name", ""),
            )
            places[place.key] = place
    return places


def write_places(path: Path, places: dict[PlaceKey, GeocodedPlace]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(places.values(), key=lambda p: (p.state, p.city.lower()))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(COLUMNS)
        for p in ordered:
            writer.writerow(
                [
                    p.city,
                    p.state,
                    f"{p.latitude:.6f}",
                    f"{p.longitude:.6f}",
                    p.source,
                    p.display_name,
                ]
            )
