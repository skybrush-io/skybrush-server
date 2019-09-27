"""Concurrency-related utility functions."""

from functools import wraps

__all__ = ("cancellable",)


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
