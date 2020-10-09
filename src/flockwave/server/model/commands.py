"""Model classes related to the asynchronous execution of commands on
UAVs.
"""

from flockwave.spec.schema import get_complex_object_schema
from inspect import iscoroutinefunction
from time import time

from .metamagic import ModelMeta
from .parameters import create_parameter_command_handler

__all__ = (
    "CommandExecutionStatus",
    "create_parameter_command_handler",
    "create_version_command_handler",
)


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
        self.error = None
        self.result = None
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

    def mark_as_finished(self, result_or_error):
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

    def mark_as_sent(self):
        """Marks the command as being sent to the UAV that will ultimately
        execute it. Also stores the current timestamp if the command has not
        been marked as sent yet. Otherwise this function is a no-op.
        """
        if self.sent is None:
            self.sent = time()


async def _version_command_handler(driver, uav) -> str:
    if iscoroutinefunction(uav.get_version_info):
        version_info = await uav.get_version_info()
    else:
        version_info = uav.get_version_info()

    if version_info:
        parts = [f"{key} = {version_info[key]}" for key in sorted(version_info.keys())]
        return "\n".join(parts)
    else:
        return "No version information available"


def create_version_command_handler():
    """Creates a generic async command handler function that allows the user to
    retrieve the version information of the UAV, assuming that the UAV
    has an async method named `get_version_info()`.

    Assign the function returned from this factory function to the
    `handle_command_version()` method of a UAVDriver_ subclass to make the
    driver support parameter retrievals and updates, assuming that the
    corresponding UAV_ object already supports it.
    """
    return _version_command_handler
