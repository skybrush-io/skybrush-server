"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from contextlib import contextmanager
from math import inf
from time import time
from trio import CancelScope, current_time
from typing import (
    Any,
    AsyncGenerator,
    Generic,
    Iterator,
    Optional,
    Union,
    TypeVar,
    overload,
)

from flockwave.concurrency import Future
from flockwave.spec.schema import get_complex_object_schema

from .metamagic import ModelMeta

__all__ = (
    "CommandExecutionStatus",
    "Progress",
    "ProgressEvents",
    "ProgressEventsWithSuspension",
    "Suspend",
)


R = TypeVar("R")
S = TypeVar("S")
T = TypeVar("T")


MISSING = object()
"""Placeholder object for the Progress_ and Suspend_ constructor so we can
distinguish between the user explicitly passing in `None` and not specifying
anything.
"""


C = TypeVar("C", bound="Progress")


class Progress(Generic[T]):
    """Progress object that may be yielded from command handlers implemented
    as an async iterator that yield one or more progress objects, optionally
    followed by a result object.
    """

    message: Optional[str]
    percentage: Optional[int]
    object: Any

    @overload
    @classmethod
    def done(cls, message: Optional[str] = None): ...

    @overload
    @classmethod
    def done(cls, message: Optional[str] = None, *, object: T): ...

    @classmethod
    def done(cls, message: Optional[str] = None, *, object: Any = MISSING):
        """Convenience constructor for a progress message with 100%
        percentage.
        """
        return cls(percentage=100, message=message, object=object)

    @overload
    def __init__(
        self,
        *,
        percentage: Optional[int] = None,
        message: Optional[str] = None,
    ): ...

    @overload
    def __init__(
        self,
        *,
        percentage: Optional[int] = None,
        message: Optional[str] = None,
        object: T,
    ): ...

    def __init__(
        self,
        *,
        percentage: Optional[int] = None,
        message: Optional[str] = None,
        object: Any = MISSING,
    ):
        self.message = message
        self.percentage = percentage
        self.object = object

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of the progress object."""
        result = {}
        if self.message is not None:
            result["message"] = str(self.message)
        if self.percentage is not None:
            result["percentage"] = int(self.percentage)
        if self.object is not MISSING:
            result["object"] = self.object
        return result

    def update(
        self: C, percentage: Optional[int] = None, message: Optional[str] = None
    ) -> C:
        """Updates the progress object with a new percentage, a new message or
        both.

        Args:
            percentage: the new percentage; `None` if it should be left
                unmodified
            message: the new message; `None` if it should be left unmodified

        Returns:
            the progress object itself for easy chaining
        """
        if percentage is not None:
            self.percentage = percentage
        if message is not None:
            self.message = message
        return self

    def __repr__(self) -> str:
        if self.object is not MISSING:
            return (
                f"{self.__class__.__name__}(percentage={self.percentage!r},"
                f"message={self.message!r}, object={self.object!r})"
            )
        else:
            return (
                f"{self.__class__.__name__}(percentage={self.percentage!r}, "
                f"message={self.message!r})"
            )


class Suspend(Generic[T]):
    """Suspension request object that may be yielded from command handlers
    implemented as an async iterator that yield one or more progress objects.
    Yielding this object will suspend the execution of the command and wait
    for additional input from the client.
    """

    message: Optional[str]
    object: Any

    @overload
    def __init__(
        self,
        *,
        message: Optional[str] = None,
    ): ...

    @overload
    def __init__(
        self,
        *,
        message: Optional[str] = None,
        object: T,
    ): ...

    def __init__(
        self,
        *,
        message: Optional[str] = None,
        object: Any = MISSING,
    ):
        self.message = message
        self.object = object

    def update(self, message: Optional[str] = None, object: Any = MISSING):
        """Updates the suspension object with a new percentage, a new object or
        both.

        Args:
            message: the new message; `None` if it should be left unmodified
            object: the new object
        """
        if message is not None:
            self.message = message
        if object is not MISSING:
            self.object = object

    def to_progress(self) -> Progress:
        return Progress(message=self.message, object=self.object)

    def __repr__(self) -> str:
        if self.object is not MISSING:
            return (
                f"{self.__class__.__name__}(message={self.message!r}, "
                f"object={self.object!r})"
            )
        else:
            return f"{self.__class__.__name__}(message={self.message!r})"


ProgressEvents = AsyncGenerator[Union[Progress[R], R], None]
"""Type alias for events that can be yielded from an async generator that
generates progress and result events.
"""

ProgressEventsWithSuspension = AsyncGenerator[Union[R, Progress[R], Suspend[S]], None]
"""Type alias for events that can be yielded from an async generator that
generates progress, suspension and result events.
"""

AsyncCommandEvents = ProgressEvents
"""Deprecated alias to ProgressEvents."""


class CommandExecutionStatus(metaclass=ModelMeta):
    """Object that stores and represents the status of the execution of
    an asynchronous command.
    """

    class __meta__:
        schema = get_complex_object_schema("commandExecutionStatus")

    id: str
    created_at: float
    client_notified: Optional[float]
    error: Optional[Exception]
    result: Any
    sent: Optional[float]
    finished: Optional[float]
    cancelled: Optional[float]
    progress: Optional[Progress]

    _cancel_scope: CancelScope
    _cancelled_by_user: bool
    _clients_to_notify: set[str]
    _deadline: float
    _suspension_future: Optional[Future[Any]]

    def __init__(self, id: str):
        """Constructor.

        Parameters:
            id: the receipt ID of this status object
        """
        self.id = id
        self.created_at = time()
        self.client_notified = None
        self.error = None
        self.result = None
        self.sent = None
        self.finished = None
        self.cancelled = None
        self.progress = None

        self._cancel_scope = CancelScope()
        self._cancelled_by_user = False
        self._clients_to_notify = set()
        self._deadline = inf
        self._suspension_future = None

    def add_client_to_notify(self, client_id: str) -> None:
        """Appends the ID of a client to notify to the list of clients
        interested in the completion of this command.
        """
        self._clients_to_notify.add(client_id)

    @property
    def clients_to_notify(self) -> set[str]:
        """Set of clients to notify when this command finishes
        execution.
        """
        return self._clients_to_notify

    def is_expired(self, now: Optional[float]) -> bool:
        """Returns whether the command execution status has expired, i.e. it
        is past its deadline.
        """
        if now is None:
            now = current_time()
        assert now is not None
        return now > self._deadline

    @property
    def is_in_progress(self) -> bool:
        """Returns whether the command is still in progress, i.e. has not
        finished and has not been cancelled yet.
        """
        return self.cancelled is None and self.finished is None

    @property
    def is_suspended(self) -> bool:
        """Returns whether the execution of the command is suspended, i.e.
        waiting for input from the client.
        """
        return self._suspension_future is not None

    @property
    def was_cancelled_by_user(self) -> bool:
        """Returns whether the command execution was cancelled by the user."""
        return self.cancelled is not None and self._cancelled_by_user

    def mark_as_cancelled(self, *, by_user: bool = True) -> bool:
        """Marks the command as being cancelled now if it has not been marked as
        cancelled yet and it has not finished either. Otherwise this function
        is a no-op.

        Returns:
            whether the command was indeed marked as cancelled now
        """
        if self.is_in_progress:
            self.cancelled = time()
            self._cancelled_by_user = bool(by_user)
            self._cancel_scope.cancel()
            return True
        else:
            return False

    def mark_as_clients_notified(self) -> None:
        """Marks that the receipt ID of the command was sent to the client that
        initially wished to execute the command.
        """
        if self.client_notified is None:
            self.client_notified = time()

    def mark_as_finished(self, result_or_error: Any) -> None:
        """Marks the command as being finished with the current timestamp if
        it has not been marked as finished yet and it has not been cancelled
        either. Otherwise this function is a no-op.

        Parameters:
            result_or_error: the result or error corresponding to the outcome
                of the command
        """
        if self.is_in_progress:
            if isinstance(result_or_error, Exception):
                self.error = result_or_error
            else:
                self.result = result_or_error
            self.finished = time()

    def mark_as_resumed(self, value: Any) -> bool:
        """Marks the command as being resumed now with the given value if the
        execution is currently suspended. Otherwise this function is a no-op.

        Returns:
            whether the command was indeed marked as resumed now
        """
        if not self.is_suspended:
            return False

        assert self._suspension_future is not None
        try:
            self._suspension_future.set_result(value)
            return True
        except RuntimeError:
            # future was already marked as done, this is a duplicate
            return False

    def mark_as_sent(self) -> None:
        """Marks the command as being sent to the UAV that will ultimately
        execute it. Also stores the current timestamp if the command has not
        been marked as sent yet. Otherwise this function is a no-op.
        """
        if self.sent is None:
            self.sent = time()

    def set_deadline_from_now(self, duration: float) -> None:
        """Sets the deadline of the execution of the command to the given number
        of seconds from now.
        """
        if not self.is_in_progress:
            raise RuntimeError("command execution has finished already")

        self._deadline = current_time() + duration
        self._cancel_scope.deadline = self._deadline

    @contextmanager
    def suspended(self, post_timeout: Optional[float] = None) -> Iterator[Future[Any]]:
        """Context manager that marks the execution of the command as suspended
        (waiting for user input) upon entering the context and resumes it when
        exiting the context.

        Args:
            post_timeout: optional timeout value to set on the command execution
                after it has been resumed

        Returns:
            a future object that the server can (and should) wait for to
            retrieve the response sent by the client
        """
        if self.is_suspended:
            raise RuntimeError("command execution is already suspended")

        self._suspension_future = Future()
        self.set_deadline_from_now(inf)
        try:
            yield self._suspension_future
        finally:
            if post_timeout is not None:
                self.set_deadline_from_now(post_timeout)
            self._suspension_future = None
