from math import inf
from trio import fail_after, TooSlowError
from trio_util import RepeatedEvent
from typing import AsyncIterator, Optional

from flockwave.server.model.commands import Progress

__all__ = ("ProgressReporter",)


class ProgressReporter:
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
    """

    _auto_close: bool
    _done: bool
    _event: RepeatedEvent
    _progress: Progress

    def __init__(self, auto_close: bool = False):
        """Constructor.

        Args:
            auto_close: whether the progress reporter should be closed
                automatically when it receives a progress report with a
                percentage greater than or equal to 100.
        """
        self._progress = Progress()
        self._event = RepeatedEvent()
        self._done = False
        self._auto_close = bool(auto_close)

    def close(self) -> None:
        """Closes the progress reporter, terminating async generators returned
        from the `updates()` method. Call this method if you are not going to
        post progress updates to this reporter any more.
        """
        self._done = True
        self._event.set()

    @property
    def done(self) -> bool:
        """Returns whether the `close()` method has already been called."""
        return self._done

    def notify(self, percentage: Optional[int] = None, message: Optional[str] = None):
        """Posts a new progress percentage and message to the progress reporter.
        Async generators returned from `updates()` will wake up and yield a new
        `Progress` instance.
        """
        # TODO(ntamas): convert this into an 'update()' method on the Progress
        # model object
        if percentage is not None:
            self._progress.percentage = percentage
        if message is not None:
            self._progress.message = message
        if self._auto_close and percentage is not None and percentage >= 100:
            self.close()
        self._event.set()

    async def updates(
        self, timeout: float = inf, fail_on_timeout: bool = True
    ) -> AsyncIterator[Progress]:
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
            return

        if self._progress.message is not None or self._progress.percentage is not None:
            yield self._progress

        events = self._event.events()

        if fail_on_timeout:
            while not self._done:
                with fail_after(timeout):
                    await events.__anext__()
                yield self._progress
        else:
            while not self._done:
                try:
                    with fail_after(timeout):
                        await events.__anext__()
                except TooSlowError:
                    self.close()
                else:
                    yield self._progress
