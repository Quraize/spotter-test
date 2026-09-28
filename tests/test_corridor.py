import numpy as np
import pytest

from apps.planner.corridor import RouteGeometry, StationIndex, find_candidates
from apps.planner.geo import MILES_PER_DEG_LAT, haversine_miles

LAT = 40.0
MILES_PER_DEG_LNG = MILES_PER_DEG_LAT * np.cos(np.radians(LAT))


def parallel_route(lng_start=-90.0, lng_end=-80.0, n=201) -> RouteGeometry:
    """A route east along latitude 40, sampled finely so it tracks the parallel."""
    lngs = np.linspace(lng_start, lng_end, n)
    return RouteGeometry(np.column_stack([lngs, np.full(n, LAT)]))


def north_of(lat: float, miles: float) -> float:
    return lat + miles / MILES_PER_DEG_LAT


def test_route_length_and_markers_on_straight_route() -> None:
    route = parallel_route()
    expected_total = 10 * MILES_PER_DEG_LNG

    assert route.total_miles == pytest.approx(expected_total, rel=0.002)

    markers, detours = route.locate(np.array([-87.5, -85.0]), np.array([LAT, LAT]))

    assert markers == pytest.approx([2.5 * MILES_PER_DEG_LNG, 5 * MILES_PER_DEG_LNG], rel=0.002)
    assert detours == pytest.approx([0.0, 0.0], abs=0.05)


def test_detour_is_geodesic_distance_from_route() -> None:
    route = parallel_route()

    markers, detours = route.locate(
        np.array([-87.5, -87.5]), np.array([north_of(LAT, 3), north_of(LAT, 20)])
    )

    assert detours == pytest.approx([3.0, 20.0], rel=0.01)
    assert markers[0] == pytest.approx(markers[1], abs=0.1)


def test_points_past_the_ends_clamp_to_the_ends() -> None:
    route = parallel_route()

    markers, detours = route.locate(np.array([-91.0, -79.0]), np.array([LAT, LAT]))

    assert markers[0] == pytest.approx(0.0, abs=1e-6)
    assert markers[1] == pytest.approx(route.total_miles, abs=1e-6)
    assert detours == pytest.approx([MILES_PER_DEG_LNG, MILES_PER_DEG_LNG], rel=0.01)


def test_markers_scale_to_provider_distance() -> None:
    plain = parallel_route()
    scaled = RouteGeometry(plain.lnglat, total_miles=plain.polyline_miles * 1.1)

    m_plain, _ = plain.locate(np.array([-85.0]), np.array([LAT]))
    m_scaled, _ = scaled.locate(np.array([-85.0]), np.array([LAT]))

    assert scaled.total_miles == pytest.approx(plain.total_miles * 1.1)
    assert m_scaled[0] == pytest.approx(m_plain[0] * 1.1)


def test_l_shaped_route_locates_on_second_leg() -> None:
    east = np.column_stack([np.linspace(-90, -85, 101), np.full(101, LAT)])
    north = np.column_stack([np.full(100, -85.0), np.linspace(LAT, LAT + 2, 101)[1:]])
    route = RouteGeometry(np.vstack([east, north]))
    first_leg = 5 * MILES_PER_DEG_LNG

    markers, detours = route.locate(np.array([-85.0]), np.array([LAT + 1]))

    assert markers[0] == pytest.approx(first_leg + MILES_PER_DEG_LAT, rel=0.002)
    assert detours[0] == pytest.approx(0.0, abs=0.05)


def test_find_candidates_filters_by_corridor_and_sorts() -> None:
    route = parallel_route()
    index = StationIndex(
        ids=[10, 20, 30, 40, 50, 60],
        lats=[LAT, north_of(LAT, 4), north_of(LAT, 12), LAT, LAT, LAT],
        lngs=[-85.0, -85.0, -85.0, -87.5, -87.5, -70.0],
        prices=[3.5, 3.0, 2.0, 3.9, 3.1, 1.0],
    )

    cands = find_candidates(route, index, corridor_miles=5)

    assert [c.station_id for c in cands] == [50, 40, 20, 10]  # by mile, then price
    assert cands[0].mile_marker == pytest.approx(2.5 * MILES_PER_DEG_LNG, rel=0.002)
    assert cands[2].detour_miles == pytest.approx(4.0, rel=0.02)
    assert all(c.detour_miles <= 5 for c in cands)


def test_find_candidates_empty_cases() -> None:
    route = parallel_route()

    assert find_candidates(route, StationIndex([], [], [], []), 5) == []
    far = StationIndex([1], [LAT + 5], [-85.0], [3.0])
    assert find_candidates(route, far, 5) == []
    with pytest.raises(ValueError):
        find_candidates(route, far, 0)


def test_route_requires_two_points() -> None:
    with pytest.raises(ValueError):
        RouteGeometry([[-90.0, 40.0]])


def test_station_index_validates_lengths() -> None:
    with pytest.raises(ValueError):
        StationIndex([1, 2], [0.0], [0.0], [1.0])


def test_real_geometry_scale_sanity() -> None:
    # Chicago -> St. Louis, densely sampled, so the midpoint vertex sits at half the distance.
    chicago, stl = (-87.63, 41.88), (-90.20, 38.63)
    pts = np.column_stack(
        [np.linspace(chicago[0], stl[0], 101), np.linspace(chicago[1], stl[1], 101)]
    )
    route = RouteGeometry(pts)
    mid = tuple(pts[50])

    markers, detours = route.locate(np.array([mid[0]]), np.array([mid[1]]))

    total = haversine_miles(chicago[1], chicago[0], stl[1], stl[0])
    assert route.total_miles == pytest.approx(total, rel=1e-3)
    assert markers[0] == pytest.approx(total / 2, rel=0.01)
    assert detours[0] < 0.01


def test_long_route_is_chunked_and_finds_stations_in_every_chunk() -> None:
    # 1,001 vertices -> 4 chunks, the last one shorter than the rest.
    route = parallel_route(n=1001)
    lngs = [-89.9, -87.0, -84.0, -81.0, -80.1]
    index = StationIndex(
        ids=list(range(len(lngs))), lats=[LAT] * len(lngs), lngs=lngs, prices=[3.0] * len(lngs)
    )

    assert len(route.chunks) == 4
    cands = find_candidates(route, index, corridor_miles=5)

    assert [c.station_id for c in cands] == [0, 1, 2, 3, 4]
    assert cands[-1].mile_marker == pytest.approx(9.9 * MILES_PER_DEG_LNG, rel=0.002)


def test_chunk_local_locate_matches_full_line() -> None:
    # Points scattered along an L-shaped 1,000-vertex route, some near chunk boundaries.
    east = np.column_stack([np.linspace(-90, -85, 501), np.full(501, LAT)])
    north = np.column_stack([np.full(500, -85.0), np.linspace(LAT, LAT + 3, 501)[1:]])
    route = RouteGeometry(np.vstack([east, north]))
    assert len(route.chunks) > 1
    rng = np.random.default_rng(1)
    lngs = np.concatenate([rng.uniform(-90, -85, 40), np.full(40, -85.0) + rng.normal(0, 0.05, 40)])
    lats = np.concatenate([LAT + rng.normal(0, 0.05, 40), rng.uniform(LAT, LAT + 3, 40)])

    markers, detours = route.locate(lngs, lats)

    # Reference: exact projection onto the whole line.
    import shapely

    from apps.planner.geo import PROJECTION

    pts = shapely.points(PROJECTION.to_xy(lngs, lats))
    ref_along = shapely.line_locate_point(route.line, pts)
    ref_near = shapely.get_coordinates(shapely.line_interpolate_point(route.line, ref_along))
    ref_lng, ref_lat = PROJECTION.to_lnglat(ref_near)
    ref_detours = haversine_miles(lats, lngs, ref_lat, ref_lng)

    assert detours == pytest.approx(ref_detours, abs=1e-6)
    assert np.all(np.diff(markers[np.argsort(ref_along)]) >= -1e-6)  # same ordering as full line
