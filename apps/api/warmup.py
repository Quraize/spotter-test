"""
Pre-load everything a request needs so the first call is as fast as the hundredth.

Called from config/wsgi.py and config/asgi.py, i.e. only when the app is actually served
(gunicorn, runserver), never for management commands. Every step is best-effort: a missing
table or data file is logged, and the request path will raise a clear error later.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def warm_up() -> None:
    from apps.routing.local_cities import get_city_index
    from apps.routing.providers import get_geocode_provider, get_route_provider
    from apps.routing.usa import is_in_usa
    from apps.stations.index import get_station_catalog

    steps = {
        "station catalog": lambda: len(get_station_catalog()),
        "city index": lambda: len(get_city_index()),
        "usa boundary": lambda: is_in_usa(38.9, -77.0),
        "providers": lambda: (get_route_provider().name, get_geocode_provider().name),
    }
    for name, step in steps.items():
        started = time.perf_counter()
        try:
            result = step()
        except Exception as exc:  # noqa: BLE001 - warm-up must never take the server down
            logger.warning("warm-up: %s failed: %s", name, exc)
        else:
            logger.info(
                "warm-up: %s ready (%s) in %.0f ms",
                name,
                result,
                (time.perf_counter() - started) * 1000,
            )
