"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from time import time
from trio import CancelScope, current_time
from typing import Any, AsyncIterator, Dict, Optional, Set, Union, TypeVar

from flockwave.spec.schema import get_complex_object_schema

from .metamagic import ModelMeta

__all__ = ("AsyncCommandEvents", "CommandExecutionStatus", "Progress")


T = TypeVar("T")


class Progress(metaclass=ModelMeta):
    """Progress object that may be yielded from command handlers implemented
    as an async iterator that yield one or more progress objects, optionally
    followed by a result object.
    """

    message: Optional[str]
    percentage: Optional[int]

    class __meta__:
        schema = get_complex_object_schema("progress")

    def __init__(
        self, *, percentage: Optional[int] = None, message: Optional[str] = None
    ):
        self.message = message
        self.percentage = percentage

    @property
    def json(self) -> Dict[str, Any]:
        """Returns the JSON representation of the progress object."""
        result = {}
        if self.message is not None:
            result["message"] = str(self.message)
        if self.percentage is not None:
            result["percentage"] = int(self.percentage)
        return result


AsyncCommandEvents = AsyncIterator[Union[Progress, T]]
"""Type alias for the return value of an async iterator that implements a
command handler with progress reporting and client-to-server messaging
support.
"""


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
    _clients_to_notify: Set[str]

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

    def add_client_to_notify(self, client_id: str) -> None:
        """Appends the ID of a client to notify to the list of clients
        interested in the completion of this command.
        """
        self._clients_to_notify.add(client_id)

    @property
    def clients_to_notify(self) -> Set[str]:
        """Set of clients to notify when this command finishes
        execution.
        """
        return self._clients_to_notify

    @property
    def is_in_progress(self) -> bool:
        """Returns whether the command is still in progress, i.e. has not
        finished and has not been cancelled yet.
        """
        return self.cancelled is None and self.finished is None

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
        self._cancel_scope.deadline = current_time() + duration
