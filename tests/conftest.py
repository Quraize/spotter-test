from pathlib import Path

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.api import services
from apps.routing.providers import CachedRouteProvider
from tests.fakes import StubResolver, StubRouter, default_catalog, synthetic_route

FUEL_CSV_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


@pytest.fixture
def fuel_csv(tmp_path: Path) -> Path:
    """A small fuel CSV exercising every cleaning rule: dupes, non-US rows, bad numbers."""
    rows = [
        '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00733333',
        '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.899',
        '20,PILOT #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.799',  # cheaper dupe
        "100,FLYING J,Hwy 1,Calgary,AB,500,4.10",  # Canada, dropped
        "101,ACI TRUCK STOP,US-46,Columbia,NJ,90,3.079",
        "102,BROKEN ROW,US-1,Nowhere,TX,1,not-a-price",  # unparseable, skipped
        "103,SHEETZ #838,I-85 Exit 71,Salisbury,NC,46,2.859",
    ]
    path = tmp_path / "fuel.csv"
    path.write_text(FUEL_CSV_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def places_csv(tmp_path: Path) -> Path:
    path = tmp_path / "places.csv"
    path.write_text(
        "city,state,latitude,longitude,source,display_name\n"
        'Big Cabin,OK,36.538000,-95.221000,local,"Big Cabin, OK, USA"\n'
        'Gila Bend,AZ,32.947800,-112.716800,local,"Gila Bend, AZ, USA"\n'
        'Columbia,NJ,40.923000,-75.070000,nominatim,"Columbia, Warren County, NJ"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch):
    """Replace the resolver, router and station catalog with in-memory fakes."""
    resolver = StubResolver()
    router = StubRouter(synthetic_route())
    catalog = default_catalog()
    monkeypatch.setattr(services, "get_resolver", lambda: resolver)
    cached = CachedRouteProvider(router, ttl_seconds=60)  # as in production: cache in front
    monkeypatch.setattr(services, "get_route_provider", lambda: cached)
    monkeypatch.setattr(services, "get_station_catalog", lambda: catalog)
    cache.clear()
    return resolver, router, catalog


@pytest.fixture
def client() -> APIClient:
    return APIClient()
