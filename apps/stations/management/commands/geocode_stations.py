"""
One-time, offline geocoding of every (city, state) pair in the fuel CSV.

Resolution order:
  1. data/us_cities.csv (local, instant)     -> source=geonames | kelvins
     If its two sources disagree on where a place is (duplicate town names in a state),
     Nominatim breaks the tie: the local candidate nearest Nominatim's answer wins, or
     Nominatim's own answer if neither is close.
  2. Nominatim, 1 request/second             -> source=nominatim

Output: data/geocoded_places.csv, committed to the repo. The command is resumable:
pairs already present in the output file are skipped, so an interrupted run can be
restarted without repeating network calls.
"""

from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.planner.geo import haversine_miles
from apps.routing.base import ProviderError
from apps.routing.local_cities import CityRecord, get_city_index
from apps.routing.nominatim import NominatimGeocoder
from apps.stations.loader import load_clean
from apps.stations.places import GeocodedPlace, place_key, read_places, write_places

NOMINATIM_MIN_INTERVAL_SECONDS = 1.1
AGREE_MILES = 5.0


class Command(BaseCommand):
    help = "Resolve coordinates for every city/state in the fuel CSV (local table, then Nominatim)."

    def add_arguments(self, parser: CommandParser) -> None:
        data_dir = Path(settings.DATA_DIR)
        parser.add_argument(
            "--csv", type=Path, default=data_dir / "fuel-prices-for-be-assessment.csv"
        )
        parser.add_argument("--output", type=Path, default=data_dir / "geocoded_places.csv")
        parser.add_argument(
            "--no-remote",
            action="store_true",
            help="Skip Nominatim; resolve from the local table only.",
        )
        parser.add_argument(
            "--refresh", action="store_true", help="Ignore the existing output file and start over."
        )

    def handle(self, *args, **options) -> None:
        csv_path: Path = options["csv"]
        output: Path = options["output"]
        remote = not options["no_remote"]

        pairs = {place_key(r.city, r.state): (r.city, r.state) for r in load_clean(csv_path)}
        places = {} if options["refresh"] else read_places(output)
        pending = {k: v for k, v in pairs.items() if k not in places}
        self.stdout.write(f"{len(pairs)} unique city/state pairs, {len(pending)} unresolved")

        index = get_city_index()
        local_hits = 0
        disputed: dict[tuple[str, str], tuple[str, str, CityRecord]] = {}
        for key, (city, state) in list(pending.items()):
            record = index.lookup(city, state)
            if record is None:
                continue
            del pending[key]
            if record.is_uncertain and remote:
                disputed[key] = (city, state, record)
                continue
            places[key] = GeocodedPlace(
                city=city,
                state=state,
                latitude=record.lat,
                longitude=record.lng,
                source=record.source,
                display_name=f"{record.city}, {record.state}, USA",
            )
            local_hits += 1
        self.stdout.write(
            f"local table resolved {local_hits}; {len(disputed)} disputed between sources; "
            f"{len(pending)} unknown"
        )
        write_places(output, places)

        if remote and disputed:
            self._settle_disputes(disputed, places, output)
        if remote and pending:
            self._geocode_remote(pending, places, output)

        unresolved = [f"{c}, {s}" for (c, s) in pending.values() if place_key(c, s) not in places]
        self.stdout.write(
            self.style.SUCCESS(f"wrote {len(places)} places to {output}")
            + (f"; unresolved: {unresolved}" if unresolved else "")
        )

    def _settle_disputes(self, disputed: dict, places: dict, output: Path) -> None:
        """Ask Nominatim which of two local candidates is the real place."""
        geocoder = NominatimGeocoder()
        try:
            for n, (key, (city, state, rec)) in enumerate(disputed.items(), start=1):
                started = time.monotonic()
                try:
                    hit = geocoder.geocode_city(city, state)
                except ProviderError as exc:
                    self.stderr.write(f"  [{n}/{len(disputed)}] {city}, {state}: {exc}")
                    hit = None
                candidates = [(rec.lat, rec.lng, rec.source)]
                if rec.alt:
                    candidates.append((rec.alt[0], rec.alt[1], "kelvins"))
                if hit is None:
                    lat, lng, source = candidates[0]
                    verdict = "no answer, kept primary"
                else:
                    dists = [
                        float(haversine_miles(hit.lat, hit.lng, c[0], c[1])) for c in candidates
                    ]
                    best = min(range(len(candidates)), key=dists.__getitem__)
                    if dists[best] <= AGREE_MILES:
                        lat, lng, source = candidates[best]
                        verdict = f"agrees with {source} ({dists[best]:.1f} mi)"
                    else:
                        lat, lng, source = hit.lat, hit.lng, "nominatim"
                        verdict = "neither local candidate within 5 mi; used nominatim"
                places[key] = GeocodedPlace(
                    city=city,
                    state=state,
                    latitude=lat,
                    longitude=lng,
                    source=source,
                    display_name=hit.name if hit else f"{rec.city}, {rec.state}, USA",
                )
                self.stdout.write(f"  [{n}/{len(disputed)}] {city}, {state}: {verdict}")
                write_places(output, places)
                self._pace(started)
        finally:
            geocoder.close()

    def _geocode_remote(self, pending: dict, places: dict, output: Path) -> None:
        geocoder = NominatimGeocoder()
        try:
            for n, (key, (city, state)) in enumerate(pending.items(), start=1):
                started = time.monotonic()
                try:
                    hit = geocoder.geocode_city(city, state)
                    if hit is None:  # e.g. "Brookpark, OH" is spelled "Brook Park" in OSM
                        time.sleep(NOMINATIM_MIN_INTERVAL_SECONDS)
                        hit = geocoder.geocode(f"{city}, {state}, USA")
                except ProviderError as exc:
                    self.stderr.write(f"  [{n}/{len(pending)}] {city}, {state}: {exc}")
                    hit = None
                if hit is None:
                    self.stderr.write(f"  [{n}/{len(pending)}] {city}, {state}: no result")
                else:
                    places[key] = GeocodedPlace(
                        city=city,
                        state=state,
                        latitude=hit.lat,
                        longitude=hit.lng,
                        source="nominatim",
                        display_name=hit.name,
                    )
                    self.stdout.write(
                        f"  [{n}/{len(pending)}] {city}, {state} -> {hit.lat:.4f},{hit.lng:.4f}"
                    )
                    write_places(output, places)  # checkpoint after every success
                self._pace(started)
        finally:
            geocoder.close()

    @staticmethod
    def _pace(started: float) -> None:
        elapsed = time.monotonic() - started
        if elapsed < NOMINATIM_MIN_INTERVAL_SECONDS:
            time.sleep(NOMINATIM_MIN_INTERVAL_SECONDS - elapsed)
