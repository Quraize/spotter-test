"""
Parsing and cleaning of the OPIS fuel price CSV. Pure functions, no Django ORM.

Cleaning rules (see PLAN.md):
  * drop rows whose State is not a US state (the file includes Canadian provinces)
  * collapse duplicate OPIS Truckstop IDs, keeping the cheapest price
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from apps.routing.local_cities import US_STATES

EXPECTED_COLUMNS = (
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
)


class FuelCsvError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StationRow:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    rack_id: int
    retail_price: Decimal


@dataclass(slots=True)
class CleanReport:
    raw_rows: int = 0
    non_us_dropped: int = 0
    invalid_dropped: int = 0
    duplicates_collapsed: int = 0
    kept: int = 0


def read_fuel_csv(path: Path) -> Iterator[StationRow]:
    """Yield every parseable row. Rows with unparseable numbers are skipped, not fatal."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in EXPECTED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise FuelCsvError(f"CSV is missing expected columns: {missing}")
        for raw in reader:
            row = _parse_row(raw)
            if row is not None:
                yield row


def _parse_row(raw: dict[str, str]) -> StationRow | None:
    try:
        return StationRow(
            opis_id=int(raw["OPIS Truckstop ID"]),
            name=raw["Truckstop Name"].strip(),
            address=raw["Address"].strip(),
            city=raw["City"].strip(),
            state=raw["State"].strip().upper(),
            rack_id=int(raw["Rack ID"]),
            retail_price=Decimal(raw["Retail Price"].strip()),
        )
    except (KeyError, ValueError, InvalidOperation, AttributeError):
        return None


def clean(rows: Iterable[StationRow], report: CleanReport | None = None) -> list[StationRow]:
    """Apply the US filter and cheapest-per-ID dedupe. Preserves first-seen order of IDs."""
    report = report if report is not None else CleanReport()
    best: dict[int, StationRow] = {}
    for row in rows:
        report.raw_rows += 1
        if row.state not in US_STATES:
            report.non_us_dropped += 1
            continue
        if row.retail_price <= 0 or not row.city:
            report.invalid_dropped += 1
            continue
        current = best.get(row.opis_id)
        if current is None:
            best[row.opis_id] = row
        else:
            report.duplicates_collapsed += 1
            if row.retail_price < current.retail_price:
                best[row.opis_id] = row
    report.kept = len(best)
    return list(best.values())


def load_clean(path: Path, report: CleanReport | None = None) -> list[StationRow]:
    return clean(read_fuel_csv(path), report)
