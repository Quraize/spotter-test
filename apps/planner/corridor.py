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

# The STRtree is queried with short pieces of the route rather than the whole polyline, so
# each distance predicate runs against a few hundred vertices instead of tens of thousands.
_CHUNK_VERTICES = 256


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
        # Overlapping pieces of the line (the last one may be shorter). Spatial queries and
        # point location run against a chunk, never the whole line: locating 500 stations on
        # a 21k-vertex cross-country line took 600 ms; on chunks it takes a few ms.
        starts = list(range(0, max(len(xy) - 1, 1), _CHUNK_VERTICES))
        self.chunks = np.array(
            [shapely.LineString(xy[i : i + _CHUNK_VERTICES + 1]) for i in starts], dtype=object
        )
        self._chunk_offsets = self._pcum[starts]

    def nearest_chunks(self, points: np.ndarray) -> np.ndarray:
        """Index of the chunk closest to each projected point (brute force over chunks)."""
        dist = shapely.distance(self.chunks[None, :], points[:, None])
        return np.argmin(dist, axis=1)

    def locate(
        self, lngs: np.ndarray, lats: np.ndarray, chunk_idx: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        For each point: (mile marker of the nearest point on the route, geodesic detour miles).

        The nearest point is found in projected space on the point's nearest chunk; the marker
        is then interpolated between the geodesic markers of that segment's endpoints, so it is
        not affected by projection distortion. ``chunk_idx`` may be supplied by the caller
        (the spatial index already knows it); otherwise it is computed.
        """
        lngs = np.asarray(lngs, dtype=float)
        lats = np.asarray(lats, dtype=float)
        if lngs.size == 0:
            return np.empty(0), np.empty(0)

        xy = PROJECTION.to_xy(lngs, lats)
        points = shapely.points(xy)
        if chunk_idx is None:
            chunk_idx = self.nearest_chunks(points)
        chunks = self.chunks[chunk_idx]
        local = shapely.line_locate_point(chunks, points)
        along = self._chunk_offsets[chunk_idx] + local

        seg = np.clip(np.searchsorted(self._pcum, along, side="right") - 1, 0, len(self._pcum) - 2)
        seg_len = self._pcum[seg + 1] - self._pcum[seg]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(seg_len > 0, (along - self._pcum[seg]) / seg_len, 0.0)
        t = np.clip(t, 0.0, 1.0)
        markers = (self.cum_miles[seg] + t * (self.cum_miles[seg + 1] - self.cum_miles[seg])) * (
            self._scale
        )

        nearest = shapely.line_interpolate_point(chunks, local)
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
        self._points = shapely.points(xy) if n else np.empty(0, dtype=object)
        self._tree = STRtree(self._points) if n else None

    def __len__(self) -> int:
        return len(self.ids)

    def near_route(
        self, route: RouteGeometry, corridor_miles: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        (station indices, nearest chunk index for each) for stations whose projected distance
        to the route is within the corridor.
        """
        if self._tree is None:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
        # dwithin runs in projected units; widen by the worst-case Mercator scale and let the
        # exact geodesic detour in find_candidates make the final cut.
        chunk_of, point_of = self._tree.query(
            route.chunks, predicate="dwithin", distance=corridor_miles * PROJECTION.max_scale
        )
        if point_of.size == 0:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
        # A station near a chunk boundary matches two chunks: keep the closer one.
        dist = shapely.distance(route.chunks[chunk_of], self._points[point_of])
        order = np.lexsort((dist, point_of))
        points, first = np.unique(point_of[order], return_index=True)
        return points, chunk_of[order][first]


def find_candidates(
    route: RouteGeometry, index: StationIndex, corridor_miles: float
) -> list[Candidate]:
    """Stations within `corridor_miles` of the route, sorted by mile marker then price."""
    if corridor_miles <= 0:
        raise ValueError("corridor_miles must be positive")
    idx, chunk_idx = index.near_route(route, corridor_miles)
    if idx.size == 0:
        return []

    markers, detours = route.locate(index.lngs[idx], index.lats[idx], chunk_idx)
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
