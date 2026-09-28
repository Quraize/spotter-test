"""
One-time, offline geocoding of every (city, state) pair in the fuel CSV.

Resolution order:
  1. data/us_cities.csv (local, instant)     -> source=local
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

from apps.routing.base import ProviderError
from apps.routing.local_cities import get_city_index
from apps.routing.nominatim import NominatimGeocoder
from apps.stations.loader import load_clean
from apps.stations.places import GeocodedPlace, place_key, read_places, write_places

NOMINATIM_MIN_INTERVAL_SECONDS = 1.1


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

        pairs = {place_key(r.city, r.state): (r.city, r.state) for r in load_clean(csv_path)}
        places = {} if options["refresh"] else read_places(output)
        pending = {k: v for k, v in pairs.items() if k not in places}
        self.stdout.write(f"{len(pairs)} unique city/state pairs, {len(pending)} unresolved")

        index = get_city_index()
        local_hits = 0
        for key, (city, state) in list(pending.items()):
            record = index.lookup(city, state)
            if record is not None:
                places[key] = GeocodedPlace(
                    city=city,
                    state=state,
                    latitude=record.lat,
                    longitude=record.lng,
                    source="local",
                    display_name=f"{record.city}, {record.state}, USA",
                )
                del pending[key]
                local_hits += 1
        self.stdout.write(f"local table resolved {local_hits}; {len(pending)} remain")
        write_places(output, places)

        if pending and not options["no_remote"]:
            self._geocode_remote(pending, places, output)

        unresolved = [f"{c}, {s}" for (c, s) in pending.values() if place_key(c, s) not in places]
        self.stdout.write(
            self.style.SUCCESS(f"wrote {len(places)} places to {output}")
            + (f"; unresolved: {unresolved}" if unresolved else "")
        )

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
                elapsed = time.monotonic() - started
                if elapsed < NOMINATIM_MIN_INTERVAL_SECONDS:
                    time.sleep(NOMINATIM_MIN_INTERVAL_SECONDS - elapsed)
        finally:
            geocoder.close()
