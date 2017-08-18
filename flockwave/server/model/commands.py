"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from datetime import datetime
from flockwave.spec.schema import get_complex_object_schema
from future.utils import with_metaclass
from time import time

from .metamagic import ModelMeta

__all__ = ("CommandExecutionStatus", )


class CommandExecutionStatus(with_metaclass(ModelMeta, object)):
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
        self.response = None
        self.sent = None
        self.finished = None
        self.cancelled = None
        self._clients_to_notify = set()

    @property
    def clients_to_notify(self):
        """Set of clients to notify when this command finishes
        execution.
        """
        return self._clients_to_notify

    def mark_as_cancelled(self):
        """Marks the command as being cancelled with the current timestamp if
        it has not been marked as cancelled yet and it has not finished
        either. Otherwise this function is a no-op.
        """
        if self.cancelled is None and self.finished is None:
            self.cancelled = datetime.now()

    def mark_as_finished(self):
        """Marks the command as being finished with the current timestamp if
        it has not been marked as finished yet and it has not been cancelled
        either. Otherwise this function is a no-op.
        """
        if self.finished is None and self.cancelled is None:
            self.finished = datetime.now()

    def mark_as_sent(self):
        """Marks the command as being sent with the current timestamp if
        it has not been marked as sent yet. Otherwise this function is a
        no-op.
        """
        if self.sent is None:
            self.sent = datetime.now()

    def notify_client(self, session_id):
        """Appends the session ID of a client to notify to the list of
        clients interested in the completion of this command.
        """
        self._clients_to_notify.add(session_id)
