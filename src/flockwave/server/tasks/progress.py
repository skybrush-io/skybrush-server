from abc import ABC, abstractmethod
from math import inf
from queue import Full, Queue
from typing import Any, Generic, TypeVar, overload

from deprecated import deprecated
from trio import (
    MemoryReceiveChannel,
    MemorySendChannel,
    TooSlowError,
    WouldBlock,
    fail_after,
    open_memory_channel,
    to_thread,
)

from flockwave.server.model.commands import (
    MISSING,
    Progress,
    ProgressEventsWithSuspension,
    Suspend,
)

__all__ = ("ProgressReporter", "ProgressReporterInterface")

R = TypeVar("R")
S = TypeVar("S")


class ProgressReporterInterface(ABC, Generic[R, S]):
    """Public interface of a progress reporter object that can be used in an async
    command handler to report progress information and suspension requests to the caller
    when the actual work is being performed by another async task or a worker thread.

    Methods of the public interface can be grouped into two groups: those that are to be
    called on the _producer_ side (the task or thread that is actually performing the
    work) and those that are to be called on the _consumer_ side (the async command
    handler that is yielding progress information to the caller).

    The consumer side (i.e. the command handler) typically works like this:

    ```
    reporter = ProgressReporter()

    # spawn an asynchronous task that will feed progress information into the reporter
    # and pass the reporter to the task so it can call its `notify()` method
    ...

    async for progress in reporter.updates():
        yield progress
    ```

    You can use the `timeout` and `fail_on_timeout` parameters of the
    `updates()` generator to implement a timeout when no progress information
    is provided in a given number of seconds. The `auto_close` parameter of the
    constructor can also be used to automatically close the progress reporter
    when a percentage equal to or larger than 100 was reported.

    The producer side uses a synchronous `notify()` method that can be called from the
    task or thread to feed progress information into the progress reporter. The
    `close()` method to let the generator know that there will be no more updates. This
    is typically used in conjunction with the `contextlib.closing()` context manager:

    ```
    with closing(reporter):
        ...
        reporter.notify(percentage=10, message="spam")
        ...
        reporter.notify(percentage=20, message="ham")
        ...
        reporter.notify(percentage=30, message="bacon")
        ...
    ```

    Suspension is also supported; the task feeding the progress reporter with
    updates can call the `suspend()` method to indicate that the operation was
    suspended and is waiting for user input. The object provided in the argument
    of `suspend()` will be forwarded to the client that initiated the operation.
    """

    @abstractmethod
    def close(self) -> None:
        """Closes the progress reporter, terminating async generators returned
        from the `updates()` method. Call this method if you are not going to
        post progress updates to this reporter any more.
        """
        ...

    @property
    @abstractmethod
    def done(self) -> bool:
        """Returns whether the `close()` method has already been called."""
        ...

    @abstractmethod
    def fail(self, message: str | Exception | None = None): ...

    @abstractmethod
    def notify(self, percentage: int | None = None, message: str | None = None): ...

    @overload
    @abstractmethod
    def suspend(self, message: str | None = None): ...

    @overload
    @abstractmethod
    def suspend(self, message: str | None = None, *, object: S): ...

    @abstractmethod
    def updates(
        self, timeout: float = inf, fail_on_timeout: bool = True
    ) -> ProgressEventsWithSuspension[R, S]: ...


class ProgressReporterBase(ProgressReporterInterface[R, S]):
    """Base class for `ProgressReporter` that provides the parts of the implementation
    that can be shared between async (event loop based) and threaded implementations.

    Subclasses of this class must specialize the implementation to make it suitable for
    either async or threaded usage.
    """

    _auto_close: bool = False
    _done: bool = False
    _error: Exception | None = None
    _suspended: bool = False

    _progress: Progress[R]
    _suspend: Suspend[S]

    def __init__(self, *, auto_close: bool = False):
        """Constructor.

        Args:
            auto_close: whether the progress reporter should be closed
                automatically when it receives a progress report with a
                percentage greater than or equal to 100.
        """
        self._progress = Progress()
        self._suspend = Suspend()
        self._event_queue_tx, self._event_queue_rx = open_memory_channel[None](0)
        self._auto_close = bool(auto_close)

    def close(self) -> None:
        """Closes the progress reporter, terminating async generators returned
        from the `updates()` method. Call this method if you are not going to
        post progress updates to this reporter any more.
        """
        self._done = True
        self._suspended = False
        self._wake_up_listeners()

    @property
    def done(self) -> bool:
        """Returns whether the `close()` method has already been called."""
        return self._done

    def fail(self, message: str | Exception | None = None):
        """Closes the progress reporter and injects an exception into the async
        generators that are currently waiting for an update in the `updates()`
        method.
        """
        if message is None:
            message = "Operation failed"

        if isinstance(message, str):
            message = RuntimeError(message)

        self._progress.update(message=str(message))
        self._error = message
        self.close()

    def notify(self, percentage: int | None = None, message: str | None = None):
        """Posts a new progress percentage and message to the progress reporter.
        Async generators returned from `updates()` will wake up and yield a
        `Progress` instance.

        When the task is suspended, calling this method will resume the task.

        You may safely call this function multiple times; `updates()` is an
        async generator and it will always yield the most recent progress or
        suspension object when it wakes up.
        """
        # TODO(ntamas): convert this into an 'update()' method on the Progress
        # model object
        self._suspended = False
        self._progress.update(percentage, message)
        if self._auto_close and percentage is not None and percentage >= 100:
            # self.close() calls self._notify() internally, so we don't need to call
            # it here
            self.close()
        else:
            self._wake_up_listeners()

    @overload
    def suspend(self, message: str | None = None): ...

    @overload
    def suspend(self, message: str | None = None, *, object: S): ...

    def suspend(self, message: str | None = None, *, object: Any = MISSING):
        """Posts a suspension notice to the progress reporter. Async generators
        returned from `updates()` will wake up and yield a `Suspend` instance.

        You may safely call this function multiple times; `updates()` is an
        async generator and it will always yield the most recent progress or
        suspension object when it wakes up.
        """
        self._suspended = True
        self._suspend.update(message, object)
        self._wake_up_listeners()

    async def updates(
        self, timeout: float = inf, fail_on_timeout: bool = True
    ) -> ProgressEventsWithSuspension[R, S]:
        """Async generator that yields `Progress` objects when a new progress
        update is posted to the progress reporter via its `notify()` method.

        The generator terminates when the `close()` method is called, or when
        a given number of seconds passes without receiving a new progress
        update.

        Args:
            timeout: maximum number of seconds to wait after a progress update
                for a new one
            fail_on_timeout: whether to raise an exception when the timeout
                passes without receiving a new progress update. When this
                parameter is `True`, `TooSlowError` will be raised upon a
                timeout. When this parameter is `False`, the generator will
                simply call the `done()` method and terminate.

        Raises:
            TooSlowError: when the timeout expires without receiving a new
                progress update and `fail_on_timeout` is set to `True`.
        """
        if self._done:
            if self._error:
                raise self._error
            else:
                return

        # Yield initial state
        if self._suspended:
            yield self._suspend
        elif (
            self._progress.message is not None or self._progress.percentage is not None
        ):
            yield self._progress

        # Yield subsequent events until the task is done or the progress
        # reporter is not needed any more
        while not self._done:
            try:
                # Timeout does not apply when the task is suspended
                with fail_after(inf if self._suspended else timeout):
                    await self._wait_for_notification()
            except TooSlowError:
                if fail_on_timeout:
                    raise
                else:
                    self.close()
            else:
                yield self._suspend if self._suspended else self._progress
                self._suspended = False

        if self._error:
            raise self._error

    @abstractmethod
    async def _wait_for_notification(self) -> None: ...

    @abstractmethod
    def _wake_up_listeners(self) -> None: ...


class _TaskBasedProgressReporter(ProgressReporterBase[R, S]):
    """Implementation of `ProgressReporter` that is designed to be used in an async
    task.
    """

    _events_tx: MemorySendChannel[None]
    _events_rx: MemoryReceiveChannel[None]

    def __init__(self, *, auto_close: bool = False):
        self._events_tx, self._events_rx = open_memory_channel[None](0)
        super().__init__(auto_close=auto_close)

    async def _wait_for_notification(self) -> None:
        await self._events_rx.receive()

    def _wake_up_listeners(self) -> None:
        try:
            self._events_tx.send_nowait(None)
        except WouldBlock:
            # no problem, the consumer is busy but it will process the event anyway
            # the next time it has time to look at the queue
            pass


class _ThreadBasedProgressReporter(ProgressReporterBase[R, S]):
    """Implementation of `ProgressReporter` that is designed to be used in a separate
    worker thread.
    """

    def __init__(self, *, auto_close: bool = False):
        self._queue = Queue[None]()
        super().__init__(auto_close=auto_close)

    async def _wait_for_notification(self) -> None:
        await to_thread.run_sync(self._queue.get)

    def _wake_up_listeners(self) -> None:
        try:
            self._queue.put_nowait(None)
        except Full:
            # no problem, the consumer is busy but it will process the event anyway
            # the next time it has time to look at the queue
            pass


class ProgressReporter(_TaskBasedProgressReporter[R, S]):
    # Compatibility alias to _TaskBasedProgressReporter for existing code that uses
    # ProgressReporter directly.

    @deprecated(
        version="2.51.0",
        reason=(
            "Use ProgressReporter.for_task() or ProgressReporter.for_thread() instead."
        ),
    )
    def __init__(self, *, auto_close: bool = False):
        super().__init__(auto_close=auto_close)

    @staticmethod
    def for_task(*, auto_close: bool = False) -> ProgressReporterInterface[R, S]:
        """Constructor.

        Creates a progress reporter object whose producer side is designed to be used in
        an async task.

        Args:
            auto_close: whether the progress reporter should be closed
                automatically when it receives a progress report with a
                percentage greater than or equal to 100.
        """
        return _TaskBasedProgressReporter(auto_close=auto_close)

    @staticmethod
    def for_thread(*, auto_close: bool = False) -> ProgressReporterInterface[R, S]:
        """Constructor.

        Creates a progress reporter object whose producer side is designed to be used in
        a separate thread.

        Args:
            auto_close: whether the progress reporter should be closed
                automatically when it receives a progress report with a
                percentage greater than or equal to 100.
        """
        return _ThreadBasedProgressReporter(auto_close=auto_close)
