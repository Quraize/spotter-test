"""
Process-level station catalog: every geocoded station in memory, plus the spatial index.

Loaded once per process (see apps.api.warmup) and reused by every request. 6.6k stations
take ~25 ms to index and a few MB of memory; hitting the database per request would be
slower than the whole planning step.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from apps.planner.corridor import StationIndex
from apps.stations.models import Station


class StationDataMissing(RuntimeError):
    """No geocoded stations in the database: `manage.py import_stations` has not been run."""


@dataclass(frozen=True, slots=True)
class StationInfo:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lng: float


class StationCatalog:
    def __init__(self, stations: list[StationInfo]) -> None:
        self.by_id = {s.opis_id: s for s in stations}
        self.index = StationIndex(
            ids=[s.opis_id for s in stations],
            lats=[s.lat for s in stations],
            lngs=[s.lng for s in stations],
            prices=[s.price for s in stations],
        )

    def __len__(self) -> int:
        return len(self.by_id)

    def get(self, opis_id: int) -> StationInfo:
        return self.by_id[opis_id]


def load_catalog() -> StationCatalog:
    rows = Station.objects.geocoded().values_list(
        "opis_id", "name", "address", "city", "state", "retail_price", "latitude", "longitude"
    )
    stations = [
        StationInfo(
            opis_id=r[0],
            name=r[1],
            address=r[2],
            city=r[3],
            state=r[4],
            price=float(r[5]),
            lat=r[6],
            lng=r[7],
        )
        for r in rows.iterator(chunk_size=2000)
    ]
    if not stations:
        raise StationDataMissing("no geocoded stations loaded; run `manage.py import_stations`")
    return StationCatalog(stations)


@lru_cache(maxsize=1)
def get_station_catalog() -> StationCatalog:
    return load_catalog()


def reset_station_catalog() -> None:
    get_station_catalog.cache_clear()
