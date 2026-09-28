"""
Corridor search: which stations lie along a route, and where.

    RouteGeometry   - a polyline with geodesic mile markers; locates arbitrary points on it
    StationIndex    - all stations in an STRtree (built once per process)
    find_candidates - stations within `corridor_miles` of the route, as sorted Candidates
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import shapely
from shapely import STRtree

from apps.planner.geo import PROJECTION, haversine_miles
from apps.planner.types import Candidate


class RouteGeometry:
    """A driving route as a (lng, lat) polyline with cumulative geodesic mile markers."""

    def __init__(self, coordinates: Sequence[Sequence[float]], total_miles: float | None = None):
        arr = np.asarray(coordinates, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] != 2:
            raise ValueError("route needs at least two (lng, lat) coordinates")
        self.lnglat = arr
        seg = haversine_miles(arr[:-1, 1], arr[:-1, 0], arr[1:, 1], arr[1:, 0])
        self.cum_miles = np.concatenate([[0.0], np.cumsum(seg)])
        self.polyline_miles = float(self.cum_miles[-1])
        # The provider's distance is authoritative (it follows the real road, the polyline is
        # a sampled approximation). Scale markers so the last one lands on it.
        self.total_miles = float(total_miles) if total_miles else self.polyline_miles
        self._scale = self.total_miles / self.polyline_miles if self.polyline_miles > 0 else 1.0

        xy = PROJECTION.to_xy(arr[:, 0], arr[:, 1])
        self.line = shapely.LineString(xy)
        pseg = np.hypot(*(np.diff(xy, axis=0).T))
        self._pcum = np.concatenate([[0.0], np.cumsum(pseg)])

    def locate(self, lngs: np.ndarray, lats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        For each point: (mile marker of the nearest point on the route, geodesic detour miles).

        The nearest point is found in projected space; the marker is then interpolated between
        the geodesic markers of that segment's endpoints, so it is not affected by projection
        distortion.
        """
        lngs = np.asarray(lngs, dtype=float)
        lats = np.asarray(lats, dtype=float)
        if lngs.size == 0:
            return np.empty(0), np.empty(0)

        xy = PROJECTION.to_xy(lngs, lats)
        points = shapely.points(xy)
        along = shapely.line_locate_point(self.line, points)

        seg = np.clip(np.searchsorted(self._pcum, along, side="right") - 1, 0, len(self._pcum) - 2)
        seg_len = self._pcum[seg + 1] - self._pcum[seg]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(seg_len > 0, (along - self._pcum[seg]) / seg_len, 0.0)
        t = np.clip(t, 0.0, 1.0)
        markers = (self.cum_miles[seg] + t * (self.cum_miles[seg + 1] - self.cum_miles[seg])) * (
            self._scale
        )

        nearest = shapely.line_interpolate_point(self.line, along)
        near_xy = shapely.get_coordinates(nearest)
        near_lng, near_lat = PROJECTION.to_lnglat(near_xy)
        detours = haversine_miles(lats, lngs, near_lat, near_lng)
        return markers, np.asarray(detours, dtype=float)


class StationIndex:
    """Spatial index over every geocoded station. Build once, query per request."""

    def __init__(
        self,
        ids: Sequence[int],
        lats: Sequence[float],
        lngs: Sequence[float],
        prices: Sequence[float],
    ) -> None:
        self.ids = np.asarray(ids, dtype=np.int64)
        self.lats = np.asarray(lats, dtype=float)
        self.lngs = np.asarray(lngs, dtype=float)
        self.prices = np.asarray(prices, dtype=float)
        n = len(self.ids)
        if not (len(self.lats) == len(self.lngs) == len(self.prices) == n):
            raise ValueError("ids, lats, lngs and prices must have the same length")
        xy = PROJECTION.to_xy(self.lngs, self.lats)
        self._tree = STRtree(shapely.points(xy)) if n else None

    def __len__(self) -> int:
        return len(self.ids)

    def near_route(self, route: RouteGeometry, corridor_miles: float) -> np.ndarray:
        """Indices of stations whose projected distance to the route is within the corridor."""
        if self._tree is None:
            return np.empty(0, dtype=np.int64)
        # dwithin runs in projected units; widen by the worst-case Mercator scale and let the
        # exact geodesic detour in find_candidates make the final cut.
        return self._tree.query(
            route.line, predicate="dwithin", distance=corridor_miles * PROJECTION.max_scale
        )


def find_candidates(
    route: RouteGeometry, index: StationIndex, corridor_miles: float
) -> list[Candidate]:
    """Stations within `corridor_miles` of the route, sorted by mile marker then price."""
    if corridor_miles <= 0:
        raise ValueError("corridor_miles must be positive")
    idx = index.near_route(route, corridor_miles)
    if idx.size == 0:
        return []

    markers, detours = route.locate(index.lngs[idx], index.lats[idx])
    keep = detours <= corridor_miles
    idx, markers, detours = idx[keep], markers[keep], detours[keep]

    order = np.lexsort((index.prices[idx], markers))
    return [
        Candidate(
            station_id=int(index.ids[i]),
            price=float(index.prices[i]),
            mile_marker=float(markers[k]),
            detour_miles=float(detours[k]),
            lat=float(index.lats[i]),
            lng=float(index.lngs[i]),
        )
        for k, i in ((k, idx[k]) for k in order)
    ]
