import numpy as np
import pytest

from apps.planner.geo import PROJECTION, haversine_miles


def test_haversine_known_distance() -> None:
    # New York City to Los Angeles, ~2,445 miles great-circle.
    d = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)

    assert d == pytest.approx(2445, rel=0.005)


def test_haversine_is_vectorised() -> None:
    d = haversine_miles(np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([0.0, 1.0]), 0.0)

    assert d[0] == 0.0
    assert d[1] == pytest.approx(69.09, rel=0.01)


def test_projection_round_trip() -> None:
    lng = np.array([-122.4, -98.0, -74.0])
    lat = np.array([37.8, 39.0, 40.7])

    back_lng, back_lat = PROJECTION.to_lnglat(PROJECTION.to_xy(lng, lat))

    assert back_lng == pytest.approx(lng, abs=1e-9)
    assert back_lat == pytest.approx(lat, abs=1e-9)


def test_projection_is_conformal_with_known_scale() -> None:
    # 10 miles north and 10 miles east of Dallas both map to 10 * scale(lat) units.
    lat, lng = 32.78, -96.8
    scale = PROJECTION.scale_at(lat)
    origin = PROJECTION.to_xy(np.array([lng]), np.array([lat]))[0]
    north = PROJECTION.to_xy(np.array([lng]), np.array([lat + 10 / 69.09]))[0]
    east_lng = lng + 10 / (69.09 * np.cos(np.radians(lat)))
    east = PROJECTION.to_xy(np.array([east_lng]), np.array([lat]))[0]

    assert np.hypot(*(north - origin)) == pytest.approx(10 * scale, rel=0.01)
    assert np.hypot(*(east - origin)) == pytest.approx(10 * scale, rel=0.01)
    assert 1.5 < PROJECTION.max_scale < 1.6
