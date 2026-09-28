"""
"Is this point in the USA?" against the real national boundary.

Backed by data/usa_boundary.geojson (Natural Earth 1:10m admin-0, public domain), buffered by
~2 km so that river-border cities, barrier islands and harbour landmarks (El Paso, Port Huron,
Miami Beach, the Statue of Liberty, Alcatraz) are never rejected. The price is that a town
directly across a narrow river (Windsor ON) is admitted too, which is harmless: rejecting a
real US place is a broken product, admitting a border town just yields a valid route. A
bounding-box check runs first so the polygon test only pays for plausible points.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from shapely.geometry import Point, shape
from shapely.prepared import PreparedGeometry, prep

TOLERANCE_DEGREES = 0.02  # ~2.2 km

# (min_lat, max_lat, min_lng, max_lng): contiguous US, Alaska both sides of the antimeridian,
# Hawaii. Cheap rejection of obviously foreign points before the polygon test.
_BOXES = (
    (24.0, 49.8, -125.5, -66.5),
    (50.5, 72.0, -180.0, -129.5),
    (50.5, 72.0, 171.5, 180.0),
    (18.5, 22.8, -161.0, -154.0),
)


@lru_cache(maxsize=1)
def _usa() -> PreparedGeometry:
    path = Path(settings.DATA_DIR) / "usa_boundary.geojson"
    with path.open(encoding="utf-8") as fh:
        feature = json.load(fh)
    geom = shape(feature["geometry"]).buffer(TOLERANCE_DEGREES, join_style="mitre")
    return prep(geom)


def is_in_usa(lat: float, lng: float) -> bool:
    if not any(a <= lat <= b and c <= lng <= d for a, b, c, d in _BOXES):
        return False
    return _usa().contains(Point(lng, lat))
