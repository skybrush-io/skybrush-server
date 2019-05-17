"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from datetime import datetime
from flockwave.spec.schema import get_complex_object_schema
from future.utils import with_metaclass
from time import time

from .metamagic import ModelMeta

__all__ = ("CommandExecutionStatus",)


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
        self.client_notified = None
        self.response = None
        self.sent = None
        self.finished = None
        self.cancelled = None

        self._callbacks = []
        self._clients_to_notify = set()

    def add_callback(self, callback):
        """Registers a callback function be called when the execution of
        the command finishes or the execution is cancelled.

        The callback will be invoked with the CommandExecutionStatus_
        object as its only argument. You can get the response corresponding
        to the CommandExecutionStatus_ by inspecting its ``response``
        property. You can also decide whether the execution was finished or
        cancelled by inspecting the ``finished`` or ``cancelled`` properties.

        Parameters:
            callback (callable): the callback function to call
        """
        self._callbacks.append(callback)

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

    def mark_as_cancelled(self):
        """Marks the command as being cancelled with the current timestamp if
        it has not been marked as cancelled yet and it has not finished
        either. Otherwise this function is a no-op.
        """
        if self.cancelled is None and self.finished is None:
            self.cancelled = datetime.now()
            self._invoke_callbacks()

    def mark_as_clients_notified(self):
        """Marks that the receipt ID of the command was sent to the client that
        initially wished to execute the command.
        """
        if self.client_notified is None:
            self.client_notified = datetime.now()

    def mark_as_finished(self):
        """Marks the command as being finished with the current timestamp if
        it has not been marked as finished yet and it has not been cancelled
        either. Otherwise this function is a no-op.
        """
        if self.finished is None and self.cancelled is None:
            self.finished = datetime.now()
            self._invoke_callbacks()

    def mark_as_sent(self):
        """Marks the command as being sent to the UAV that will ultimately
        execute it. Also stores the current timestamp if the command has not
        been marked as sent yet. Otherwise this function is a no-op.
        """
        if self.sent is None:
            self.sent = datetime.now()

    def _invoke_callbacks(self):
        """Invokes all the registered callbacks and clears the callback list.

        This function is called when the command execution finishes or when
        the command execution is cancelled.
        """
        callbacks = self._callbacks
        self._callbacks = []
        for callback in callbacks:
            callback(self)
