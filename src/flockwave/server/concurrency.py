"""Concurrency-related utility functions."""

from collections.abc import Mapping
from functools import partial, wraps
from trio import Cancelled, Event, WouldBlock
from trio_util import MailboxRepeatedEvent
from typing import Any, Callable, Generic, Iterable, Iterator, TypeVar

__all__ = ("AsyncBundler", "cancellable", "Future", "FutureCancelled")


T = TypeVar("T")


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


class FutureCancelled(RuntimeError):
    """Exception raised when trying to retrieve the result of a cancelled
    future.

    Note that it is fundamentally different from a Trio Cancelled_ error so
    it deserves its own exception class. For instance, calling
    `await future.wait()` raises Cancelled_ if the await operation itself
    was cancelled, but it raises FutureCancelled_ if the await operation
    finished but the future itself was cancelled in some other task.
    """

    pass


class Future(Generic[T]):
    """Object representing the result of a computation that is to be completed
    later.

    This object is essentially a Trio Event_ with an associated value. A Trio
    task may await on the result of the future while another one performs the
    computation and sets the value of the future when the computation is
    complete.
    """

    def __init__(self):
        self._event = Event()
        self._cancelled = False
        self._value = None
        self._error = None

    def cancel(self) -> bool:
        """Cancels the future.

        Returns:
            `True` if the future was _cancelled_, `False` if the future is
            already _done_ or _cancelled_.
        """
        if self._event.is_set():
            return False

        self._cancelled = True
        self._event.set()

        return True

    async def call(self, func, *args, **kwds):
        """Calls the given function, waits for its result and sets the result
        in the future.

        If the function throws an exception, sets the exception in the future.
        """
        self._ensure_not_done()
        try:
            self.set_result(await func(*args, **kwds))
        except Cancelled:
            self.cancel()
            raise
        except Exception as ex:
            self.set_exception(ex)

    def cancelled(self) -> bool:
        """Returns whether the future is done."""
        return self._cancelled

    def done(self) -> bool:
        """Returns whether the future is done."""
        return self._event.is_set()

    def exception(self) -> Exception:
        """Returns the exception that was set on this future.

        The exception (or `None` if no exception was set) is returned only if
        the future is _done_.

        Raises:
            FutureCancelled: if the future was cancelled
            WouldBlock: if the result of the future is not yet available
        """
        self._check_done_or_cancelled()
        return self._error

    def result(self) -> T:
        """Returns the result of the future.

        If the future is _done_ and has a result set by the `set_result()` method,
        the result value is returned.

        If the future is _done_ and has an exception set by the `set_exception()`
        method, this method raises the exception.

        Raises:
            FutureCancelled: if the future was cancelled
            WouldBlock: if the result of the future is not yet available
        """
        self._check_done_or_cancelled()
        if self._error:
            raise self._error
        else:
            return self._value

    def set_exception(self, exception: Exception) -> None:
        """Marks the future as _done_ and sets an exception.

        Raises:
            RuntimeError: if the future is already done
        """
        self._ensure_not_done()
        self._error = exception
        self._event.set()

    def set_result(self, value: T) -> None:
        """Marks the future as _done_ and sets its result.

        Raises:
            RuntimeError: if the future is already done
        """
        self._ensure_not_done()
        self._value = value
        self._event.set()

    async def wait(self) -> T:
        """Waits until the future is resolved, and then returns the value
        assigned to the future.

        If the execution behind the future yielded an exception, raises the
        exception itself.

        Returns:
            the value of the future

        Raises:
            FutureCancelled: if the future was cancelled
        """
        await self._event.wait()
        return self.result()

    def _check_done_or_cancelled(self) -> None:
        if not self._event.is_set():
            raise WouldBlock()

        if self._cancelled:
            raise FutureCancelled()

    def _ensure_not_done(self) -> None:
        if self._event.is_set():
            raise RuntimeError("future is already done")


class FutureMap(Mapping, Generic[T]):
    """Dictionary that maps arbitrary string keys to futures that are resolved
    to concrete values at a later time.

    You may not add new futures to the map directly; you need to use the
    `new()` method to add a new future. The method is a context manager; the
    future is kept in the map as long as the execution is inside the context.
    Also, the context will block upon exiting if the future is not done yet,
    and remove the future from the map after exiting the context.

    The typical use-case of this map is as follows:

    ```
    map = FutureMap()
    with map.new() as future:
        # pass the future to some other, already running task that will
        # eventually resolve it
        result = await future
        # do something with the result
    ```
    """

    class Context:
        def __init__(self, future: Future[T], disposer: Callable[[], None]):
            self._disposer = disposer
            self._future = future

        async def __aenter__(self):
            return self._future

        async def __aexit__(self, exc_type, exc_value, tb):
            self._disposer()
            if exc_type is None:
                await self._future.wait()

    def __init__(self, factory: Callable[[], Future[T]] = Future[T]):
        """Constructor.

        Parameters:
            factory: callable that can be used to create a new Future_ when
                invoked with no arguments
        """
        self._factory = factory
        self._futures = {}

    def __getitem__(self, key) -> Future[T]:
        return self._futures[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._futures)

    def __len__(self) -> int:
        return len(self._futures)

    def _dispose_future(self, id: str, future: Future) -> None:
        if not future.done():
            future.cancel()

        existing_future = self._futures.get(id)
        if existing_future is future:
            del self._futures[id]

    def new(self, id: str, strict: bool = False) -> "FutureMap.Context":
        old_future = self._futures.get(id)

        if old_future:
            if strict:
                raise RuntimeError("Another operation is already in progress")
            else:
                self._dispose_future(id, old_future)

        self._futures[id] = future = self._factory()
        return self.Context(future, partial(self._dispose_future, id, future))
