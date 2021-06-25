"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from flockwave.spec.schema import get_complex_object_schema
from time import time
from typing import Any, Callable, Optional, Set

from .metamagic import ModelMeta

__all__ = ("CommandExecutionStatus",)


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

    _clients_to_notify: Set[str]
    _on_cancelled: Optional[Callable[[], None]]

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

        self._clients_to_notify = set()
        self._on_cancelled = None

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

    def mark_as_cancelled(self) -> None:
        """Marks the command as being cancelled with the current timestamp if
        it has not been marked as cancelled yet and it has not finished
        either. Otherwise this function is a no-op.
        """
        if self.is_in_progress:
            self.cancelled = time()
        if self._on_cancelled:
            self._on_cancelled()

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

    def when_cancelled(self, func: Callable[[], None]) -> None:
        """Registers the given function to be called when the command execution
        is cancelled.

        Overrides any previously registered function when called multiple times.

        Parameters:
            func: the function to call.
        """
        self._on_cancelled = func
