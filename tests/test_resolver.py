from pathlib import Path

import pytest
from django.core.cache import cache

from apps.routing.base import Place, ProviderError
from apps.routing.local_cities import LocalCityIndex
from apps.routing.providers import CachedGeocodeProvider, get_resolver, reset_providers
from apps.routing.resolver import LocationNotFound, LocationResolver
from apps.routing.usa import is_in_usa


@pytest.fixture
def cities(tmp_path: Path) -> LocalCityIndex:
    csv_path = tmp_path / "cities.csv"
    csv_path.write_text(
        "ID,STATE_CODE,STATE_NAME,CITY,COUNTY,LATITUDE,LONGITUDE\n"
        "1,IL,Illinois,Chicago,Cook,41.8781,-87.6298\n"
        "2,IL,Illinois,Springfield,Sangamon,39.7817,-89.6501\n"
        "3,MO,Missouri,Springfield,Greene,37.2090,-93.2923\n"
        "4,TX,Texas,Dallas,Dallas,32.7767,-96.7970\n"
        "5,NM,New Mexico,Las Cruces,Dona Ana,32.3199,-106.7637\n",
        encoding="utf-8",
    )
    return LocalCityIndex(csv_path)


class RecordingGeocoder:
    name = "stub"

    def __init__(self, result: Place | None | Exception = None) -> None:
        self.result = result
        self.queries: list[str] = []

    def geocode(self, query: str) -> Place | None:
        self.queries.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


SPRINGFIELD_MO = Place("Springfield, MO, USA", 37.2090, -93.2923, "ors")


@pytest.fixture
def geocoder() -> RecordingGeocoder:
    return RecordingGeocoder(SPRINGFIELD_MO)


@pytest.fixture
def resolver(cities: LocalCityIndex, geocoder: RecordingGeocoder) -> LocationResolver:
    return LocationResolver(cities=cities, geocoder=geocoder)


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["41.8781,-87.6298", " 41.8781 , -87.6298 ", "+41.8781,-87.6298"])
def test_coordinates_resolve_without_geocoder(resolver, geocoder, text) -> None:
    place = resolver.resolve(text)

    assert place == Place("41.87810, -87.62980", 41.8781, -87.6298, "coords")
    assert geocoder.queries == []


def test_coordinates_out_of_range_rejected(resolver) -> None:
    with pytest.raises(LocationNotFound, match="out of range"):
        resolver.resolve("95,-87")


def test_coordinates_outside_usa_rejected(resolver) -> None:
    with pytest.raises(LocationNotFound, match="outside the USA"):
        resolver.resolve("43.65,-79.38")  # Toronto


def test_integer_looking_input_is_not_treated_as_coordinates(resolver, geocoder) -> None:
    resolver.resolve("60601")  # a ZIP goes to the geocoder

    assert geocoder.queries == ["60601"]


# ---------------------------------------------------------------------------
# Local city table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Chicago, IL",
        "chicago, il",
        "Chicago , Illinois",
        "Chicago, IL, USA",
        "Chicago, IL, United States",
        "Chicago IL",
        "Chicago Illinois",
        "Chicago",  # only one Chicago in the table
    ],
)
def test_city_state_variants_resolve_locally(resolver, geocoder, text) -> None:
    place = resolver.resolve(text)

    assert place == Place("Chicago, IL, USA", 41.8781, -87.6298, "local")
    assert geocoder.queries == []


def test_multi_word_city_with_state_resolves_locally(resolver, geocoder) -> None:
    place = resolver.resolve("Las Cruces NM")

    assert place.name == "Las Cruces, NM, USA"
    assert geocoder.queries == []


def test_ambiguous_city_goes_to_geocoder(resolver, geocoder) -> None:
    place = resolver.resolve("Springfield")

    assert place == SPRINGFIELD_MO
    assert geocoder.queries == ["Springfield"]


def test_city_with_state_beats_ambiguity(resolver, geocoder) -> None:
    place = resolver.resolve("Springfield, IL")

    assert place.name == "Springfield, IL, USA"
    assert geocoder.queries == []


def test_bare_state_name_goes_to_geocoder_not_a_same_named_town(resolver, geocoder, cities) -> None:
    # The table has a village called "Texas" in some state; a bare state must not match it.
    resolver.resolve("Texas")
    resolver.resolve("tx")

    assert geocoder.queries == ["Texas", "tx"]


def test_unknown_city_state_pair_falls_back_to_geocoder(resolver, geocoder) -> None:
    resolver.resolve("Brook Park, OH")

    assert geocoder.queries == ["Brook Park, OH"]


def test_unknown_state_text_falls_back_to_geocoder(resolver, geocoder) -> None:
    resolver.resolve("Chicago, Ontario")

    assert geocoder.queries == ["Chicago, Ontario"]


# ---------------------------------------------------------------------------
# Live geocoder
# ---------------------------------------------------------------------------


def test_street_address_goes_to_geocoder_with_original_text(resolver, geocoder) -> None:
    resolver.resolve("  1600 Pennsylvania   Ave NW, Washington, DC ")

    assert geocoder.queries == ["1600 Pennsylvania Ave NW, Washington, DC"]


def test_geocoder_miss_is_not_found(cities) -> None:
    resolver = LocationResolver(cities, RecordingGeocoder(None))

    with pytest.raises(LocationNotFound, match="no match"):
        resolver.resolve("Atlantis")


def test_geocoder_result_outside_usa_is_not_found(cities) -> None:
    toronto = Place("Toronto, ON, Canada", 43.65, -79.38, "ors")
    resolver = LocationResolver(cities, RecordingGeocoder(toronto))

    with pytest.raises(LocationNotFound, match="outside the USA"):
        resolver.resolve("Toronto")


def test_geocoder_errors_propagate(cities) -> None:
    resolver = LocationResolver(cities, RecordingGeocoder(ProviderError("down")))

    with pytest.raises(ProviderError):
        resolver.resolve("Somewhere St")


def test_no_geocoder_configured(cities) -> None:
    resolver = LocationResolver(cities, geocoder=None)

    assert resolver.resolve("Dallas, TX").source == "local"
    with pytest.raises(LocationNotFound, match="no geocoder"):
        resolver.resolve("123 Main St, Dallas, TX")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_input_rejected(resolver, text) -> None:
    with pytest.raises(ValueError, match="empty"):
        resolver.resolve(text)


def test_overlong_input_rejected(resolver) -> None:
    with pytest.raises(ValueError, match="at most"):
        resolver.resolve("x" * 201)


# ---------------------------------------------------------------------------
# USA bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lng", "inside"),
    [
        (41.88, -87.63, True),  # Chicago
        (61.22, -149.9, True),  # Anchorage
        (21.31, -157.86, True),  # Honolulu
        (51.88, -176.65, True),  # Adak, Aleutians
        (31.7619, -106.4850, True),  # El Paso, on the Rio Grande border
        (42.9709, -82.4249, True),  # Port Huron, on the St. Clair River border
        (25.79, -80.13, True),  # Miami Beach, barrier island
        (38.3365, -75.0849, True),  # Ocean City MD, barrier island
        (42.3314, -83.0458, True),  # Detroit
        (40.6892, -74.0445, True),  # Statue of Liberty, in the harbour
        (37.8267, -122.4230, True),  # Alcatraz
        (33.3428, -118.3282, True),  # Avalon, Catalina Island
        (42.3149, -83.0364, True),  # Windsor ON: 1 km across the river, admitted by design
        (31.6904, -106.4245, False),  # Ciudad Juarez, across the river from El Paso
        (32.5149, -117.0382, False),  # Tijuana
        (49.28, -123.12, False),  # Vancouver BC
        (43.65, -79.38, False),  # Toronto
        (19.43, -99.13, False),  # Mexico City
        (51.5, -0.12, False),  # London
        (0.0, 0.0, False),  # null island
    ],
)
def test_is_in_usa(lat, lng, inside) -> None:
    assert is_in_usa(lat, lng) is inside


# ---------------------------------------------------------------------------
# Geocode cache and factory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean():
    cache.clear()
    reset_providers()
    yield
    cache.clear()
    reset_providers()


def test_cached_geocoder_hits_inner_once_and_caches_misses() -> None:
    inner = RecordingGeocoder(SPRINGFIELD_MO)
    cached = CachedGeocodeProvider(inner, ttl_seconds=60)

    assert cached.geocode("Springfield") == SPRINGFIELD_MO
    assert cached.geocode("  springfield ") == SPRINGFIELD_MO
    assert inner.queries == ["Springfield"]

    inner_miss = RecordingGeocoder(None)
    cached_miss = CachedGeocodeProvider(inner_miss, ttl_seconds=60)
    assert cached_miss.geocode("Atlantis") is None
    assert cached_miss.geocode("Atlantis") is None
    assert inner_miss.queries == ["Atlantis"]


def test_resolver_factory_uses_real_city_table_and_provider_chain(settings) -> None:
    settings.GEOCODING_PROVIDERS = ["nominatim"]
    reset_providers()

    resolver = get_resolver()

    assert resolver.resolve("Dallas, TX").source == "local"
    assert resolver.geocoder.name == "nominatim"
    assert get_resolver() is resolver


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("Philly", "Philadelphia, PA"),
        ("NYC", "New York, NY"),
        ("LA", "Los Angeles, CA"),
        ("D.C.", "Washington, DC"),
    ],
)
def test_nicknames_map_to_cities(cities, geocoder, alias, expected) -> None:
    # The fixture table lacks these cities, so a successful alias shows up as the expanded
    # name reaching the geocoder instead of the raw nickname.
    LocationResolver(cities, geocoder).resolve(alias)

    assert geocoder.queries[-1] == expected


def test_trailing_punctuation_is_ignored(resolver, geocoder) -> None:
    assert resolver.resolve("Chicago, IL.").name == "Chicago, IL, USA"
    assert resolver.resolve("Dallas, TX;").name.startswith("Dallas")
    assert geocoder.queries == []
