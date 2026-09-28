"""
Build data/us_cities.csv from two public sources, keeping a record of where they disagree.

    GeoNames cities500  (CC BY 4.0)  populated places >= 500 people, with population, so the
                                     most populous match wins for duplicate names in a state.
    kelvins/US-Cities-Database (MIT) ~30k places incl. very small ones GeoNames lacks; its
                                     coordinates are ZIP-derived and occasionally far off.

Preferred coordinates are GeoNames when present, else kelvins. Where both exist and differ
by more than AGREEMENT_MILES the row is flagged, and `geocode_stations` asks a third source.

Run once; the output is committed. Downloads ~14 MB from geonames.org.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.routing.local_cities import US_STATES, normalize_city

GEONAMES_CITIES = "https://download.geonames.org/export/dump/cities500.zip"
GEONAMES_ADMIN1 = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
KELVINS_CSV = "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
AGREEMENT_MILES = 5.0

COLUMNS = (
    "ID",
    "STATE_CODE",
    "STATE_NAME",
    "CITY",
    "COUNTY",
    "LATITUDE",
    "LONGITUDE",
    "POPULATION",
    "SOURCE",
    "ALT_LATITUDE",
    "ALT_LONGITUDE",
    "DISAGREEMENT_MILES",
)


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


class Command(BaseCommand):
    help = "Rebuild data/us_cities.csv from GeoNames + kelvins (one-time, ~14 MB download)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output", type=Path, default=Path(settings.DATA_DIR) / "us_cities.csv"
        )
        parser.add_argument("--cache-dir", type=Path, default=None, help="Reuse downloads here.")

    def handle(self, *args, **options) -> None:
        cache = options["cache_dir"]
        cities_zip = self._fetch(GEONAMES_CITIES, cache, "cities500.zip")
        admin1 = self._fetch(GEONAMES_ADMIN1, cache, "admin1CodesASCII.txt").decode("utf-8")
        kelvins = self._fetch(KELVINS_CSV, cache, "kelvins_us_cities.csv").decode("utf-8")

        state_names = {}
        for line in admin1.splitlines():
            code, _name, ascii_name, _gid = line.split("\t")
            if code.startswith("US."):
                state_names[code[3:]] = ascii_name

        geo: dict[tuple[str, str], dict] = {}
        with zipfile.ZipFile(io.BytesIO(cities_zip)) as z:
            text = z.read("cities500.txt").decode("utf-8")
        for line in io.StringIO(text):
            f = line.rstrip("\n").split("\t")
            if f[8] != "US" or f[6] != "P" or f[10] not in US_STATES:
                continue
            row = {
                "name": f[2] or f[1],
                "state": f[10],
                "lat": float(f[4]),
                "lng": float(f[5]),
                "pop": int(f[14] or 0),
                "county": "",
            }
            for nm in {f[1], f[2]}:
                key = (normalize_city(nm), f[10])
                if key not in geo or row["pop"] > geo[key]["pop"]:
                    geo[key] = {**row, "name": nm.strip()}

        kel: dict[tuple[str, str], dict] = {}
        for r in csv.DictReader(io.StringIO(kelvins)):
            st = r["STATE_CODE"].strip().upper()
            if st not in US_STATES:
                continue
            key = (normalize_city(r["CITY"]), st)
            kel.setdefault(
                key,
                {
                    "name": r["CITY"].strip(),
                    "state": st,
                    "lat": float(r["LATITUDE"]),
                    "lng": float(r["LONGITUDE"]),
                    "county": r["COUNTY"].strip(),
                    "state_name": r["STATE_NAME"].strip(),
                },
            )
            state_names.setdefault(st, r["STATE_NAME"].strip())

        out_rows = []
        flagged = 0
        for key in sorted(set(geo) | set(kel)):
            g, k = geo.get(key), kel.get(key)
            primary = g or k
            alt = k if g else None
            disagreement = ""
            if g and k:
                d = haversine(g["lat"], g["lng"], k["lat"], k["lng"])
                disagreement = f"{d:.1f}"
                flagged += d > AGREEMENT_MILES
            out_rows.append(
                [
                    len(out_rows) + 1,
                    key[1],
                    state_names.get(key[1], ""),
                    primary["name"],
                    (k or {}).get("county", ""),
                    f"{primary['lat']:.6f}",
                    f"{primary['lng']:.6f}",
                    (g or {}).get("pop", ""),
                    "geonames" if g else "kelvins",
                    f"{alt['lat']:.6f}" if alt else "",
                    f"{alt['lng']:.6f}" if alt else "",
                    disagreement,
                ]
            )

        output: Path = options["output"]
        with output.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(COLUMNS)
            w.writerows(out_rows)
        self.stdout.write(
            self.style.SUCCESS(
                f"wrote {len(out_rows)} places to {output}: {len(geo)} from GeoNames, "
                f"{len(out_rows) - len(geo)} only in kelvins, {flagged} flagged "
                f"(sources > {AGREEMENT_MILES:g} mi apart)"
            )
        )

    def _fetch(self, url: str, cache: Path | None, name: str) -> bytes:
        if cache is not None:
            path = cache / name
            if path.exists():
                return path.read_bytes()
        self.stdout.write(f"downloading {url}")
        data = httpx.get(url, timeout=120, follow_redirects=True).raise_for_status().content
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_bytes(data)
        return data
