"""
Per-request counter of external HTTP calls.

The assignment cares about how many times the map/routing API is hit per request, so we
count every outbound provider call in a context variable and report it in the response.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

_calls: ContextVar[list[str]] = ContextVar("external_calls")


@contextmanager
def track_calls() -> Iterator[list[str]]:
    """Collect the names of provider calls made inside the block."""
    token = _calls.set([])
    try:
        yield _calls.get()
    finally:
        _calls.reset(token)


def record_call(name: str) -> None:
    with suppress(LookupError):  # outside track_calls(): nothing to record
        _calls.get().append(name)
