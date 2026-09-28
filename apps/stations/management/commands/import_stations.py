"""
Load the fuel CSV into the Station table, joined with the geocoded places file.

Idempotent: re-running upserts on OPIS ID. Runs in a few seconds; no network access.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from apps.stations.loader import CleanReport, load_clean
from apps.stations.models import Station
from apps.stations.places import place_key, read_places

UPDATE_FIELDS = (
    "name",
    "address",
    "city",
    "state",
    "rack_id",
    "retail_price",
    "latitude",
    "longitude",
    "geocode_source",
)


class Command(BaseCommand):
    help = "Import fuel stations from the OPIS CSV (dedupe, US-only, join coordinates)."

    def add_arguments(self, parser: CommandParser) -> None:
        data_dir = Path(settings.DATA_DIR)
        parser.add_argument(
            "--csv", type=Path, default=data_dir / "fuel-prices-for-be-assessment.csv"
        )
        parser.add_argument("--places", type=Path, default=data_dir / "geocoded_places.csv")

    def handle(self, *args, **options) -> None:
        report = CleanReport()
        rows = load_clean(options["csv"], report)
        places = read_places(options["places"])
        if not places:
            self.stderr.write(
                self.style.WARNING(
                    f"no geocoded places at {options['places']}; stations will have no "
                    "coordinates. Run `manage.py geocode_stations` first."
                )
            )

        stations: list[Station] = []
        without_coords = 0
        for row in rows:
            place = places.get(place_key(row.city, row.state))
            if place is None:
                without_coords += 1
            stations.append(
                Station(
                    opis_id=row.opis_id,
                    name=row.name,
                    address=row.address,
                    city=row.city,
                    state=row.state,
                    rack_id=row.rack_id,
                    retail_price=row.retail_price,
                    latitude=place.latitude if place else None,
                    longitude=place.longitude if place else None,
                    geocode_source=place.source if place else Station.GeocodeSource.NONE,
                )
            )

        with transaction.atomic():
            Station.objects.bulk_create(
                stations,
                batch_size=500,
                update_conflicts=True,
                unique_fields=["opis_id"],
                update_fields=list(UPDATE_FIELDS),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"imported {report.kept} stations "
                f"(raw {report.raw_rows}, non-US dropped {report.non_us_dropped}, "
                f"invalid dropped {report.invalid_dropped}, duplicates collapsed "
                f"{report.duplicates_collapsed}, without coordinates {without_coords})"
            )
        )
