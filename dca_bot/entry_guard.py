"""Process-local serialization for paper-entry paths in the single-worker app deployment.

The manual API and autonomous scanner both perform read-check-write entry flows.
This shared re-entrant lock prevents those flows from passing the same stale
active-deal/allocation checks concurrently within the supported single process.
Run the app with one ASGI worker; this lock is not a distributed database lock.
"""
from __future__ import annotations

from functools import wraps
from threading import RLock
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

_ENTRY_LOCK = RLock()


def serialized_entry(func: Callable[P, R]) -> Callable[P, R]:
    """Serialize a complete paper-entry path across API and worker threads."""
    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with _ENTRY_LOCK:
            return func(*args, **kwargs)

    return wrapped
