from math import inf
from trio import fail_after, TooSlowError
from trio_util import RepeatedEvent
from typing import Any, Generic, Optional, TypeVar, Union, overload

from flockwave.server.model.commands import (
    Progress,
    ProgressEventsWithSuspension,
    Suspend,
    MISSING,
)

__all__ = ("ProgressReporter",)

R = TypeVar("R")
S = TypeVar("S")


class ProgressReporter(Generic[R, S]):
    """Helper class for reporting progress from within a command handler if the
    progress is provided by another asynchronous task.

    This class provides a synchronous `notify()` method that can be called from
    another task to feed progress information into the progress reporter, and
    an asynchronous `updates()` generator that yields `Progress` objects
    that are suitable to be yielded further from an asynchronous command
    handler in a UAV driver instance.

    The typical usage pattern in the command handler is as follows:

    ```
    reporter = ProgressReporter()
    async for progress in reporter.updates():
        yield progress
    ```

    You can use the `timeout` and `fail_on_timeout` parameters of the
    `updates()` generator to implement a timeout when no progress information
    is provided in a given number of seconds. The `auto_close` parameter of the
    constructor can also be used to automatically close the progress reporter
    when a percentage equal to or larger than 100 was reported.

    Other tasks that wish to feed progress information into a running
    `updates()` generator can call the `notify()` method to update the progress
    and the `close()` method to let the generator know that there will be no
    more updates. This is typically used in conjunction with the
    `contextlib.closing()` context manager:

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

    _auto_close: bool = False
    _done: bool = False
    _error: Optional[Exception] = None
    _suspended: bool = False

    _event: RepeatedEvent
    _progress: Progress[R]
    _suspend: Suspend[S]

    def __init__(self, auto_close: bool = False):
        """Constructor.

        Args:
            auto_close: whether the progress reporter should be closed
                automatically when it receives a progress report with a
                percentage greater than or equal to 100.
        """
        self._progress = Progress()
        self._suspend = Suspend()
        self._event = RepeatedEvent()
        self._auto_close = bool(auto_close)

    def close(self) -> None:
        """Closes the progress reporter, terminating async generators returned
        from the `updates()` method. Call this method if you are not going to
        post progress updates to this reporter any more.
        """
        self._done = True
        self._suspended = False
        self._event.set()

    @property
    def done(self) -> bool:
        """Returns whether the `close()` method has already been called."""
        return self._done

    def fail(self, message: Optional[Union[str, Exception]] = None):
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

    def notify(self, percentage: Optional[int] = None, message: Optional[str] = None):
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
            self.close()
        self._event.set()

    @overload
    def suspend(self, message: Optional[str] = None): ...

    @overload
    def suspend(self, message: Optional[str] = None, *, object: S): ...

    def suspend(self, message: Optional[str] = None, *, object: Any = MISSING):
        """Posts a suspension notice to the progress reporter. Async generators
        returned from `updates()` will wake up and yield a `Suspend` instance.

        You may safely call this function multiple times; `updates()` is an
        async generator and it will always yield the most recent progress or
        suspension object when it wakes up.
        """
        self._suspended = True
        self._suspend.update(message, object)
        self._event.set()

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
        events = self._event.events()
        while not self._done:
            try:
                # Timeout does not apply when the task is suspended
                with fail_after(inf if self._suspended else timeout):
                    await events.__anext__()
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
