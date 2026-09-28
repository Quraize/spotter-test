"""
Provider assembly: fallback chains, route caching, and settings-driven factories.

    get_route_provider()    -> Cached(Fallback([ors, osrm]))     per ROUTING_PROVIDERS
    get_geocode_provider()  -> Fallback([ors, nominatim])        per GEOCODING_PROVIDERS

Providers are process-level singletons so their HTTP connection pools are reused.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache

from apps.routing.base import GeocodeProvider, Place, ProviderError, Route, RouteProvider
from apps.routing.http import ProviderClientError
from apps.routing.nominatim import NominatimGeocoder
from apps.routing.ors import ORSClient
from apps.routing.osrm import OSRMRouter

logger = logging.getLogger(__name__)


def _should_fall_through(exc: ProviderError) -> bool:
    """Try the next provider unless the failure says our input itself was bad."""
    return not (isinstance(exc, ProviderClientError) and exc.is_bad_input)


class FallbackRouteProvider:
    """Tries each provider in order; a later one only runs if the earlier one failed."""

    def __init__(self, providers: Sequence[RouteProvider]) -> None:
        if not providers:
            raise ValueError("at least one route provider is required")
        self.providers = list(providers)
        self.name = "+".join(p.name for p in self.providers)

    def route(self, start: Place, finish: Place) -> Route:
        last: ProviderError | None = None
        for provider in self.providers:
            try:
                return provider.route(start, finish)
            except ProviderError as exc:
                last = exc
                if not _should_fall_through(exc):
                    break
                logger.warning("route provider %s failed: %s", provider.name, exc)
        assert last is not None
        raise last


class FallbackGeocodeProvider:
    def __init__(self, providers: Sequence[GeocodeProvider]) -> None:
        if not providers:
            raise ValueError("at least one geocode provider is required")
        self.providers = list(providers)
        self.name = "+".join(p.name for p in self.providers)

    def geocode(self, query: str) -> Place | None:
        last: ProviderError | None = None
        for provider in self.providers:
            try:
                return provider.geocode(query)
            except ProviderError as exc:
                last = exc
                if not _should_fall_through(exc):
                    break
                logger.warning("geocode provider %s failed: %s", provider.name, exc)
        assert last is not None
        raise last


class CachedRouteProvider:
    """
    Memoises routes by rounded endpoints (4 decimals, ~11 m) in the Django cache.

    Identical demo requests, and the map page that follows an API call, never hit the
    routing API twice.
    """

    def __init__(self, inner: RouteProvider, ttl_seconds: int | None = None) -> None:
        self.inner = inner
        self.name = inner.name
        self.ttl = ttl_seconds if ttl_seconds is not None else settings.ROUTE_CACHE_SECONDS

    def cache_key(self, start: Place, finish: Place) -> str:
        raw = f"{self.name}|{start.lat:.4f},{start.lng:.4f}|{finish.lat:.4f},{finish.lng:.4f}"
        return "route:" + hashlib.sha1(raw.encode()).hexdigest()

    def route(self, start: Place, finish: Place) -> Route:
        key = self.cache_key(start, finish)
        cached = cache.get(key)
        if isinstance(cached, Route):
            return cached
        route = self.inner.route(start, finish)
        cache.set(key, route, self.ttl)
        return route


_ROUTE_FACTORIES: dict[str, Callable[[], RouteProvider]] = {
    "ors": ORSClient,
    "osrm": OSRMRouter,
}
_GEOCODE_FACTORIES: dict[str, Callable[[], GeocodeProvider]] = {
    "ors": ORSClient,
    "nominatim": NominatimGeocoder,
}


def _build(names: Sequence[str], factories: dict, kind: str) -> list:
    built = []
    for name in names:
        try:
            built.append(factories[name]())
        except KeyError:
            raise ValueError(
                f"unknown {kind} provider {name!r}; choose from {sorted(factories)}"
            ) from None
        except ProviderError as exc:  # e.g. ORS without a key: skip, keep the rest
            logger.warning("%s provider %s unavailable: %s", kind, name, exc)
    if not built:
        raise ValueError(f"no usable {kind} provider among {list(names)}")
    return built


@lru_cache(maxsize=1)
def get_route_provider() -> RouteProvider:
    chain = _build(settings.ROUTING_PROVIDERS, _ROUTE_FACTORIES, "routing")
    return CachedRouteProvider(FallbackRouteProvider(chain))


@lru_cache(maxsize=1)
def get_geocode_provider() -> GeocodeProvider:
    chain = _build(settings.GEOCODING_PROVIDERS, _GEOCODE_FACTORIES, "geocoding")
    return FallbackGeocodeProvider(chain)


def reset_providers() -> None:
    """For tests and settings changes."""
    get_route_provider.cache_clear()
    get_geocode_provider.cache_clear()
