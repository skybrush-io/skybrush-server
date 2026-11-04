"""Merging policies that determine what happens when multiple events of the
same type are dispatched within the coalescing window of the `events`
extension.
"""

from collections.abc import Callable, Sequence
from typing import Any

from typing_extensions import TypeVar

__all__ = ("MergingPolicy", "keep_all", "keep_first", "keep_latest")


T = TypeVar("T", default=Any)
"""Type variable representing the type of the bodies of events."""

MergingPolicy = Callable[[Sequence[T], T | None], Sequence[T]]
"""Type specification for merging policies, generic over the type of the
bodies of events.

A merging policy is a function that receives the bodies of the events of a
given type that were dispatched but not sent to the subscribers yet (in the
order they were dispatched), and the body of a newly dispatched event of the
same type. It must return the new list of pending event bodies. Each item in
the returned list is sent as a separate event to the subscribers. Returning an
empty list cancels all pending events of the given type.

Event bodies are ``None`` for events that have no body.
"""

# Note that the built-in merging policies below are typed with `Any` instead of
# being generic over `T`; this makes them compatible with dispatchers of any
# body type without confusing the type inference of `use()`.


def keep_all(pending: Sequence[Any], body: Any) -> Sequence[Any]:
    """Merging policy that keeps all the events, i.e. no events are dropped."""
    return [*pending, body]


def keep_first(pending: Sequence[Any], body: Any) -> Sequence[Any]:
    """Merging policy that keeps the first event within the coalescing window
    and drops all subsequent ones.
    """
    return pending if pending else [body]


def keep_latest(pending: Sequence[Any], body: Any) -> Sequence[Any]:
    """Merging policy that keeps the latest event within the coalescing window
    and drops all earlier ones. This is the default merging policy.
    """
    return [body]
