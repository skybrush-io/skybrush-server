"""Helper functions shared by the rate limiter tests."""

from collections.abc import Iterable
from typing import Any, cast

from flockwave.server.model.messages import FlockwaveMessage

__all__ = ("async_noop", "tuple_message_factory")


async def async_noop(*args, **kwds):
    """Async function that does nothing."""
    pass


def tuple_message_factory(items: Iterable[Any]) -> FlockwaveMessage:
    """Message factory for rate limiters that returns the items as a tuple.

    Rate limiters expect factories that return a FlockwaveMessage. The tests
    only compare the dispatched items, and a tuple is enough for that. The
    cast keeps the type checker happy.
    """
    return cast(FlockwaveMessage, tuple(items))
