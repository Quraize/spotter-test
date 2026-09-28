"""
Geodesy helpers: great-circle distance and a planar projection for spatial indexing.

Mile markers and detours are always computed with the haversine formula. The projection is
only used to build a spatial index and to locate the nearest point on a polyline.
"""

from __future__ import annotations

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEG_LAT = 2 * np.pi * EARTH_RADIUS_MILES / 360

# Continental US spans roughly 24N..49N; Mercator scale there is 1/cos(lat) <= 1.53.
CONUS_MAX_LAT = 50.0


def haversine_miles(
    lat1: np.ndarray | float,
    lng1: np.ndarray | float,
    lat2: np.ndarray | float,
    lng2: np.ndarray | float,
) -> np.ndarray | float:
    """Great-circle distance in miles. Vectorised over numpy arrays."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = phi2 - phi1
    dlmb = np.radians(np.asarray(lng2) - np.asarray(lng1))
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


class MercatorProjection:
    """
    Spherical Mercator in miles-at-the-equator.

    Mercator is conformal: locally it is a pure uniform scaling, so the nearest point on a
    polyline in projected space is also the nearest point on the ground. That is exactly the
    property corridor search needs. The scale factor is 1/cos(lat); distance thresholds in
    projected space must be widened by ``max_scale`` and then checked geodesically.
    """

    def __init__(self, max_lat: float = CONUS_MAX_LAT) -> None:
        self.max_scale = 1.0 / np.cos(np.radians(max_lat))

    def to_xy(self, lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
        lng = np.asarray(lng, dtype=float)
        lat = np.clip(np.asarray(lat, dtype=float), -85.0, 85.0)
        x = np.radians(lng) * EARTH_RADIUS_MILES
        y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * EARTH_RADIUS_MILES
        return np.column_stack([x, y])

    def to_lnglat(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        xy = np.asarray(xy, dtype=float)
        lng = np.degrees(xy[:, 0] / EARTH_RADIUS_MILES)
        lat = np.degrees(2 * np.arctan(np.exp(xy[:, 1] / EARTH_RADIUS_MILES)) - np.pi / 2)
        return lng, lat

    def scale_at(self, lat: np.ndarray | float) -> np.ndarray | float:
        """Projected units per ground mile at this latitude."""
        return 1.0 / np.cos(np.radians(lat))


PROJECTION = MercatorProjection()
