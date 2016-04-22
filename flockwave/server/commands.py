"""Classes related to the asynchronous execution of commands on
remote UAVs.
"""

from blinker import Signal
from flask import request
from greenlet import GreenletExit
from eventlet import sleep, spawn_n
from six import iteritems
from time import time

from .logger import log as base_log
from .model import CommandExecutionStatus, CommandExecutionStatusBuilder, \
    RegistryBase

__all__ = ("CommandExecutionManager", )

log = base_log.getChild("commands")


class CommandExecutionManager(RegistryBase):
    """Manager that is responsible for keeping track of commands that are
    currently being executed on remote UAVs.

    The manager provides the following functionality:

      - Creates CommandExecutionStatus_ objects and ensures the uniqueness
        of their identifiers.

      - Detects when a command execution has timed out and cleans up the
        list of pending CommandExecutionStatus_ objects periodically.

    Attributes:
        cancelled (Signal): signal that is emitted when an asynchronous
            command has been cancelled by the user. The signal conveys the
            status object that corresponds to the cancelled command.
        expired (Signal): signal that is emitted when one or more
            asynchronous commands have timed out and the corresponding
            status objects are now considered as expired. The signal
            conveys the *list* of status objects that have expired.
        finished (Signal): signal that is emitted when the execution of an
            asynchronous command finishes. The signal conveys the status
            object that corresponds to the finished command.
        timeout (float): the number of seconds that must pass since
            the start of a command to consider the command as having
            timed out. The status items corresponding to these commands
            will be removed from the execution manager at the next
            invocation of ``cleanup()``.
    """

    cancelled = Signal()
    expired = Signal()
    finished = Signal()

    def __init__(self, timeout=30):
        """Constructor.

        Parameters:
            timeout (float): the number of seconds that must pass since
                the start of a command to consider the command as having
                timed out.
        """
        super(CommandExecutionManager, self).__init__()
        self._builder = CommandExecutionStatusBuilder()
        self._cleanup_greenlet = spawn_n(self._cleanup_loop)
        self.timeout = timeout

    def __del__(self):
        self._cleanup_greenlet.kill()

    def cleanup(self):
        """Runs a cleanup process on the dictionary containing the commands
        currently in progress, removing items that have expired and those
        for which the command has finished its execution and the result has
        been sent via a ``finished`` signal.

        An item is considered to be expired if it has been created more than
        ``self.timeout`` seconds ago and it has not finished execution yet.
        """
        commands = self._entries
        expiry = time() - self.timeout
        to_remove = [key for key, status in iteritems(commands)
                     if status.finished is not None or
                     status.cancelled is not None or
                     status.created_at < expiry]
        if to_remove:
            expired = [commands.pop(key) for key in to_remove]
            expired = [command for command in expired
                       if command.finished is None and
                       command.cancelled is None]
            self.expired.send(self, statuses=expired)

    def _cleanup_loop(self, seconds=1):
        """Runs the cleanup process periodically in an infinite loop. This
        method should be launched in a background greenlet.

        Parameters:
            second (int or float): number of seconds to wait between
                consecutive cleanups.
        """
        while True:
            sleep(seconds)
            try:
                self.cleanup()
            except GreenletExit:
                break
            except Exception as ex:
                log.exception(ex)

    def cancel(self, receipt_id):
        """Cancels the execution of the asynchronous command with the given
        receipt ID.

        Parameters:
            receipt_id (str or CommandExecutionStatus): the receipt
                identifier of the command that was finished, or the
                execution status object of the command itself.

        Throws:
            ValueError: if the given receipt belongs to a different manager
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile
            log.warn("Received cancellation request for expired receipt: "
                     "{0}".format(receipt_id))
            return

        command.mark_as_cancelled()
        self.cancelled.send(self, status=command)

    def finish(self, receipt_id, result=None):
        """Marks the asynchronous command with the given receipt identifier
        as finished, optionally adding the given object as a result.

        Parameters:
            receipt_id (str or CommandExecutionStatus): the receipt
                identifier of the command that was finished, or the
                execution status object of the command itself.
            result (Optional[object]): the result of the command

        Throws:
            ValueError: if the given receipt belongs to a different manager
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile
            log.warn("Received response for expired receipt: "
                     "{0}".format(receipt_id))
            return

        command.mark_as_finished()
        command.response = result
        self.finished.send(self, status=command)

    def start(self):
        """Registers the execution of a new asynchronous command in the
        command manager.

        This method should be used by UAV drivers when they start executing
        an asynchronous command to obtain a CommandExecutionStatus_ object.

        The currently connected client (if any) will be registered as the
        requestor of the command so it will be notified when the execution
        of the command finishes.

        Returns:
            CommandExecutionStatus: a newly created CommandExecutionStatus_
                object for the asynchronous command that the driver has
                started to execute.
        """
        result = self._builder.create_status_object()
        result.notify_client(request.sid)
        result.mark_as_sent()
        self._entries[result.id] = result
        return result

    def _get_command_from_id(self, receipt_id):
        """Returns the command execution status object corresponding to the
        given receipt ID, or, if the function is given a
        CommandExecutionStatus_ object, checks whether the object really
        belongs to this manager.

        Parameters:
            receipt_id (str or CommandExecutionStatus): the receipt
                identifier of the command execution status object being
                looked up, or the execution status object of the command
                itself.

        Returns:
            CommandExecutionStatus: the command execution status object
                belonging to the given receipt ID.
        """
        if isinstance(receipt_id, CommandExecutionStatus):
            receipt = self._entries.get(receipt_id.id)
            if receipt is not None and receipt != receipt_id:
                raise ValueError("receipt does not belong to this manager")
        else:
            receipt = self._entries.get(receipt_id)
        return receipt
