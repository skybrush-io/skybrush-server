"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from flockwave.spec.schema import get_complex_object_schema
from time import time

from .metamagic import ModelMeta

__all__ = ("CommandExecutionStatus",)


class CommandExecutionStatus(metaclass=ModelMeta):
    """Object that stores and represents the status of the execution of
    an asynchronous command.
    """

    class __meta__:
        schema = get_complex_object_schema("commandExecutionStatus")

    def __init__(self, id=None):
        """Constructor.

        Parameters:
            id (str): the receipt ID of this status object
        """
        self.id = id
        self.created_at = time()
        self.client_notified = None
        self.response = None
        self.sent = None
        self.finished = None
        self.cancelled = None

        self._clients_to_notify = set()

    def add_client_to_notify(self, client_id):
        """Appends the ID of a client to notify to the list of clients
        interested in the completion of this command.
        """
        self._clients_to_notify.add(client_id)

    @property
    def clients_to_notify(self):
        """Set of clients to notify when this command finishes
        execution.
        """
        return self._clients_to_notify

    @property
    def is_in_progress(self):
        """Returns whether the command is still in progress, i.e. has not
        finished and has not been cancelled yet.
        """
        return self.cancelled is None and self.finished is None

    def mark_as_cancelled(self):
        """Marks the command as being cancelled with the current timestamp if
        it has not been marked as cancelled yet and it has not finished
        either. Otherwise this function is a no-op.
        """
        if self.is_in_progress:
            self.cancelled = time()

    def mark_as_clients_notified(self):
        """Marks that the receipt ID of the command was sent to the client that
        initially wished to execute the command.
        """
        if self.client_notified is None:
            self.client_notified = time()

    def mark_as_finished(self):
        """Marks the command as being finished with the current timestamp if
        it has not been marked as finished yet and it has not been cancelled
        either. Otherwise this function is a no-op.
        """
        if self.is_in_progress:
            self.finished = time()

    def mark_as_sent(self):
        """Marks the command as being sent to the UAV that will ultimately
        execute it. Also stores the current timestamp if the command has not
        been marked as sent yet. Otherwise this function is a no-op.
        """
        if self.sent is None:
            self.sent = time()
