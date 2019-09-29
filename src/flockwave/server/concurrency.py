"""Concurrency-related utility functions."""

from functools import wraps
from trio_util import MailboxRepeatedEvent
from typing import Any, Iterable

__all__ = ("AsyncBundler", "cancellable")


def cancellable(func):
    """Decorator that extends an async function with an extra `cancel_scope`
    keyword argument and makes the function enter the cancel scope.
    """

    @wraps(func)
    async def decorated(*args, cancel_scope, **kwds):
        with cancel_scope:
            return await func(*args, **kwds)

    decorated._cancellable = True

    return decorated


class AsyncBundler:
    """Asynchronous object that holds a bundle and supports the following
    operations:

    - Adding one or more items to the bundle

    - Waiting for the bundle to become non-empty and then removing all items
      from the bundle in one operation.

    This object is typically used in a producer-consumer setting. Producers
    add items to the bundle either one by one (with `add()`) or in batches
    (with `add_many()`). At the same time, a single consumer iterates over
    the bundle asynchronously and takes all items from it in each iteration.
    """

    def __init__(self):
        """Constructor."""
        self._data = set()
        self._event = MailboxRepeatedEvent()

    def add(self, item: Any) -> None:
        """Adds a single item to the bundle.

        Parameters:
            item: the item to add
        """
        self._data.add(item)
        self._event.set()

    def add_many(self, items: Iterable[Any]) -> None:
        """Adds multiple items to the bundle from an iterable.

        Parameters:
            items: the items to add
        """
        self._data.update(items)
        if self._data:
            self._event.set()

    def clear(self) -> None:
        """Clears all the items currently waiting in the bundle."""
        self._data.clear()

    async def __aiter__(self):
        """Asynchronously iterates over non-empty batches of items that
        were added to the set.
        """
        async for _ in self._event:
            result = set(self._data)
            self._data.clear()
            yield result
