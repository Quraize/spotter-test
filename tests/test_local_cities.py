from pathlib import Path

import pytest

from apps.routing.local_cities import LocalCityIndex, get_city_index, normalize_city


@pytest.fixture
def index(tmp_path: Path) -> LocalCityIndex:
    csv_path = tmp_path / "cities.csv"
    csv_path.write_text(
        "ID,STATE_CODE,STATE_NAME,CITY,COUNTY,LATITUDE,LONGITUDE\n"
        "1,IL,Illinois,Springfield,Sangamon,39.7817,-89.6501\n"
        "2,MO,Missouri,Springfield,Greene,37.2090,-93.2923\n"
        "3,IL,Illinois,Chicago,Cook,41.8781,-87.6298\n"
        "4,IL,Illinois,Chicago,Cook,41.0,-87.0\n"  # duplicate: first wins
        "5,ON,Ontario,Toronto,York,43.65,-79.38\n",  # not a US state: ignored
        encoding="utf-8",
    )
    return LocalCityIndex(csv_path)


def test_lookup_is_case_and_whitespace_insensitive(index: LocalCityIndex) -> None:
    hit = index.lookup("  chicago ", "il")

    assert hit is not None
    assert (hit.lat, hit.lng) == (41.8781, -87.6298)


def test_first_duplicate_wins_and_non_us_ignored(index: LocalCityIndex) -> None:
    assert len(index) == 3
    assert index.lookup("Toronto", "ON") is None


def test_candidates_detects_ambiguity(index: LocalCityIndex) -> None:
    states = {c.state for c in index.candidates("Springfield")}

    assert states == {"IL", "MO"}
    assert index.candidates("Atlantis") == []


def test_as_place_carries_local_source(index: LocalCityIndex) -> None:
    place = index.lookup("Chicago", "IL").as_place()

    assert place.source == "local"
    assert place.name == "Chicago, IL, USA"
    assert place.lnglat == (-87.6298, 41.8781)


def test_normalize_city() -> None:
    assert normalize_city("  Big   Cabin ") == "big cabin"


def test_real_dataset_loads_and_resolves_known_cities() -> None:
    index = get_city_index()

    assert len(index) > 25_000
    dallas = index.lookup("Dallas", "TX")
    assert dallas is not None
    assert 32 < dallas.lat < 33 and -97 < dallas.lng < -96
