from decimal import Decimal
from pathlib import Path

import pytest

from apps.stations.loader import CleanReport, FuelCsvError, clean, load_clean, read_fuel_csv


def test_read_skips_unparseable_rows(fuel_csv: Path) -> None:
    rows = list(read_fuel_csv(fuel_csv))

    assert [r.opis_id for r in rows] == [7, 20, 20, 100, 101, 103]
    assert rows[0].retail_price == Decimal("3.00733333")
    assert rows[0].address == "I-44, EXIT 283 & US-69"


def test_read_rejects_wrong_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("id,name\n1,x\n", encoding="utf-8")

    with pytest.raises(FuelCsvError):
        list(read_fuel_csv(bad))


def test_clean_drops_non_us_and_keeps_cheapest_duplicate(fuel_csv: Path) -> None:
    report = CleanReport()

    rows = load_clean(fuel_csv, report)

    by_id = {r.opis_id: r for r in rows}
    assert set(by_id) == {7, 20, 101, 103}
    assert by_id[20].retail_price == Decimal("3.799")
    assert by_id[20].name == "PILOT #1243"
    assert report.raw_rows == 6
    assert report.non_us_dropped == 1
    assert report.duplicates_collapsed == 1
    assert report.kept == 4


def test_clean_preserves_first_seen_order(fuel_csv: Path) -> None:
    rows = load_clean(fuel_csv)

    assert [r.opis_id for r in rows] == [7, 20, 101, 103]


def test_clean_drops_non_positive_price() -> None:
    from apps.stations.loader import StationRow

    rows = [StationRow(1, "X", "a", "City", "TX", 1, Decimal("0"))]
    report = CleanReport()

    assert clean(rows, report) == []
    assert report.invalid_dropped == 1
