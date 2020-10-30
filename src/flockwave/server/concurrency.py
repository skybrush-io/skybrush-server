"""Concurrency-related utility functions."""

from functools import partial, wraps
from inspect import iscoroutine, iscoroutinefunction
from trio import open_nursery, sleep
from typing import Any, Callable, Dict, TypeVar

from flockwave.server.utils import identity

__all__ = (
    "aclosing",
    "delayed",
    "race",
)


T = TypeVar("T")


class aclosing:
    """Context manager that closes an async generator when the context is
    exited. Similar to `closing()` in `contextlib`.
    """

    def __init__(self, aiter):
        self._aiter = aiter

    async def __aenter__(self):
        return self._aiter

    async def __aexit__(self, *args):
        await self._aiter.aclose()


def delayed(seconds: float, fn=None, *, ensure_async=False):
    """Decorator or decorator factory that delays the execution of a
    synchronous function, coroutine or coroutine-returning function with a
    given number of seconds.

    Parameters:
        seconds: the number of seconds to delay with. Negative numbers will
            throw an exception. Zero will return the identity function.
        fn: the function, coroutine or coroutine-returning function to delay
        ensure_async: when set to `True`, synchronous functions will automatically
            be converted to asynchronous before delaying them. This is needed
            if the delayed function is going to be executed in the async
            event loop because a synchronous function that sleeps will block
            the entire event loop.

    Returns:
        the delayed function, coroutine or coroutine-returning function
    """
    if seconds < 0:
        raise ValueError("delay must not be negative")
    elif seconds == 0:
        return identity

    if fn is None:
        return partial(delayed, seconds, ensure_async=ensure_async)

    if iscoroutinefunction(fn):

        @wraps(fn)
        async def decorated(*args, **kwds):
            await sleep(seconds)
            return await fn(*args, **kwds)

    elif iscoroutine(fn):

        async def decorated():
            await sleep(seconds)
            return fn

        decorated = decorated()

    elif ensure_async:

        async def decorated(*args, **kwds):
            await sleep(seconds)
            return fn(*args, **kwds)

    else:

        def decorated(*args, **kwds):
            sleep(seconds)
            return fn(*args, **kwds)

    return decorated


async def race(funcs: Dict[str, Callable[[], Any]]):
    """Run multiple async functions concurrently and wait for at least one of
    them to complete. Return the key corresponding to the function and the
    result of the function as well.
    """
    holder = []

    async with open_nursery() as nursery:
        cancel = nursery.cancel_scope.cancel
        for key, func in funcs.items():
            set_result = partial(_cancel_and_set_result, cancel, holder, key)
            nursery.start_soon(_wait_and_call, func, set_result)

    return holder[0]


def _cancel_and_set_result(cancel, holder, key, value):
    holder.append((key, value))
    cancel()


async def _wait_and_call(f1, f2) -> None:
    """Call an async function f1() and wait for its result. Call synchronous
    function `f2()` with the result of `f1()` when `f1()` returns.
    """
    result = await f1()
    f2(result)
