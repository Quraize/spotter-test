from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.routing.base import Place
from apps.routing.local_cities import LocalCityIndex
from apps.stations.models import Station
from apps.stations.places import GeocodedPlace, read_places, write_places


@pytest.mark.django_db
def test_import_stations_joins_coordinates_and_is_idempotent(
    fuel_csv: Path, places_csv: Path
) -> None:
    out = StringIO()
    call_command("import_stations", csv=fuel_csv, places=places_csv, stdout=out)

    assert Station.objects.count() == 4
    assert Station.objects.geocoded().count() == 3
    gila = Station.objects.get(opis_id=20)
    assert gila.retail_price == Decimal("3.799")
    assert (gila.latitude, gila.longitude) == (32.9478, -112.7168)
    assert gila.geocode_source == Station.GeocodeSource.LOCAL
    assert Station.objects.get(opis_id=101).geocode_source == Station.GeocodeSource.NOMINATIM
    salisbury = Station.objects.get(opis_id=103)
    assert salisbury.latitude is None
    assert salisbury.geocode_source == Station.GeocodeSource.NONE
    assert "without coordinates 1" in out.getvalue()

    # Re-import: same rows, no duplicates, updated values win.
    fuel_csv.write_text(
        fuel_csv.read_text(encoding="utf-8").replace("3.00733333", "2.50"), encoding="utf-8"
    )
    call_command("import_stations", csv=fuel_csv, places=places_csv, stdout=StringIO())

    assert Station.objects.count() == 4
    assert Station.objects.get(opis_id=7).retail_price == Decimal("2.50")


@pytest.mark.django_db
def test_import_without_places_file_warns_but_imports(fuel_csv: Path, tmp_path: Path) -> None:
    err = StringIO()
    call_command(
        "import_stations",
        csv=fuel_csv,
        places=tmp_path / "missing.csv",
        stderr=err,
        stdout=StringIO(),
    )

    assert Station.objects.count() == 4
    assert Station.objects.geocoded().count() == 0
    assert "geocode_stations" in err.getvalue()


def test_places_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "places.csv"
    places = {
        ("big cabin", "OK"): GeocodedPlace(
            "Big Cabin", "OK", 36.538, -95.221, "local", "Big Cabin, OK"
        ),
        ("columbia", "NJ"): GeocodedPlace(
            "Columbia", "NJ", 40.923, -75.07, "nominatim", "Columbia, NJ"
        ),
    }

    write_places(path, places)
    loaded = read_places(path)

    assert loaded == places
    assert read_places(tmp_path / "nope.csv") == {}


class FakeGeocoder:
    """Stands in for NominatimGeocoder: structured lookup misses, free text hits."""

    calls: list[tuple[str, ...]] = []

    def geocode_city(self, city: str, state: str) -> Place | None:
        self.calls.append(("city", city, state))
        return None

    def geocode(self, query: str) -> Place | None:
        self.calls.append(("free", query))
        return Place(name="Salisbury, NC, USA", lat=35.67, lng=-80.47, source="nominatim")

    def close(self) -> None:
        pass


def test_geocode_stations_resolves_locally_then_remotely(
    fuel_csv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.stations.management.commands import geocode_stations as cmd

    FakeGeocoder.calls = []
    monkeypatch.setattr(cmd, "NominatimGeocoder", FakeGeocoder)
    monkeypatch.setattr(cmd, "NOMINATIM_MIN_INTERVAL_SECONDS", 0)
    # Local table knows every city in the fixture except Salisbury, NC.
    cities = tmp_path / "cities.csv"
    cities.write_text(
        "ID,STATE_CODE,STATE_NAME,CITY,COUNTY,LATITUDE,LONGITUDE\n"
        "1,OK,Oklahoma,Big Cabin,Craig,36.538,-95.221\n"
        "2,AZ,Arizona,Gila Bend,Maricopa,32.9478,-112.7168\n"
        "3,NJ,New Jersey,Columbia,Warren,40.923,-75.07\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cmd, "get_city_index", lambda: LocalCityIndex(cities))
    output = tmp_path / "geocoded.csv"

    call_command(
        "geocode_stations", csv=fuel_csv, output=output, stdout=StringIO(), stderr=StringIO()
    )

    places = read_places(output)
    assert len(places) == 4
    assert places[("gila bend", "AZ")].source == "local"
    assert places[("salisbury", "NC")].source == "nominatim"
    assert FakeGeocoder.calls == [("city", "Salisbury", "NC"), ("free", "Salisbury, NC, USA")]

    # Resumable: a second run finds nothing pending and makes no remote calls.
    FakeGeocoder.calls = []
    call_command(
        "geocode_stations", csv=fuel_csv, output=output, stdout=StringIO(), stderr=StringIO()
    )
    assert FakeGeocoder.calls == []
