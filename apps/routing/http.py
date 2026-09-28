"""Shared HTTP plumbing for external providers: one client factory, one error translation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from django.conf import settings

from apps.routing.base import ProviderError, ProviderTimeout
from apps.routing.calls import record_call

logger = logging.getLogger(__name__)


def make_client(base_url: str, headers: dict[str, str] | None = None) -> httpx.Client:
    """A pooled client with hard timeouts and our User-Agent. Providers own its lifetime."""
    merged = {"User-Agent": settings.HTTP_USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return httpx.Client(
        base_url=base_url,
        headers=merged,
        timeout=httpx.Timeout(settings.EXTERNAL_TIMEOUT_SECONDS, connect=5.0),
        follow_redirects=False,
    )


class ProviderClientError(ProviderError):
    """4xx from a provider. Not retried against the same provider."""

    def __init__(self, provider: str, status_code: int, detail: str) -> None:
        super().__init__(f"{provider}: HTTP {status_code} {detail}".rstrip())
        self.status_code = status_code
        self.detail = detail

    @property
    def is_bad_input(self) -> bool:
        """400/404/422 mean our request was wrong; another provider will reject it too."""
        return self.status_code in (400, 404, 422)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    provider: str,
    retry_once: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Perform a request and return parsed JSON.

    Retries once on connection errors, timeouts and 5xx. 4xx is not retried: it means bad
    input or an exhausted quota, and a second identical request will not help.
    """

    def attempt() -> Any:
        record_call(provider)
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{provider}: timed out ({exc.__class__.__name__})") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{provider}: transport error ({exc.__class__.__name__})") from exc

        if response.status_code >= 500:
            raise ProviderError(f"{provider}: upstream HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _client_error(provider, response)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{provider}: non-JSON response") from exc

    return _with_retry(attempt, provider) if retry_once else attempt()


def _client_error(provider: str, response: httpx.Response) -> ProviderClientError:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message", ""))
        elif isinstance(err, str):
            detail = err
        detail = detail or str(body.get("message", ""))
    return ProviderClientError(provider, response.status_code, detail[:200])


def _with_retry(fn: Callable[[], Any], provider: str) -> Any:
    try:
        return fn()
    except ProviderClientError:
        raise
    except ProviderError as exc:
        logger.warning("%s failed once (%s); retrying", provider, exc)
        return fn()
