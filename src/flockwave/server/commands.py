"""Classes related to the asynchronous execution of commands on
remote UAVs.
"""

from blinker import Signal
from inspect import isawaitable
from time import time
from trio import (
    current_time,
    open_memory_channel,
    open_nursery,
    CancelScope,
    Nursery,
    TooSlowError,
)
from trio.abc import ReceiveChannel, SendChannel
from trio_util import periodic
from typing import Any, Awaitable, Optional, Union, Tuple

from .logger import log as base_log
from .model.builders import CommandExecutionStatusBuilder
from .model.commands import CommandExecutionStatus
from .registries.base import RegistryBase

__all__ = ("CommandExecutionManager",)

log = base_log.getChild("commands")


ReceiptLike = Union[CommandExecutionStatus, str]
Result = Union[Any, Awaitable[Any]]


class CommandExecutionManager(RegistryBase[CommandExecutionStatus]):
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

    timeout: float

    _tx_queue: SendChannel[Tuple[Result, CommandExecutionStatus]]
    _rx_queue: ReceiveChannel[Tuple[Result, CommandExecutionStatus]]

    def __init__(self, timeout: float = 30):
        """Constructor.

        Parameters:
            timeout: the number of seconds that must pass since the start of a
                command to consider the command as having timed out.
        """
        super().__init__()
        self._builder = CommandExecutionStatusBuilder()
        self._tx_queue, self._rx_queue = open_memory_channel(0)
        self.timeout = timeout

    def cancel(self, receipt_id: ReceiptLike) -> None:
        """Cancels the execution of the asynchronous command with the given
        receipt ID.

        Parameters:
            receipt_id: the receipt identifier of the command that should be
                cancelled
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile
            log.warn(
                "Received cancellation request for non-existent receipt: "
                "{0}".format(receipt_id)
            )
            return

        command.mark_as_cancelled()
        self.cancelled.send(self, status=command)

    def is_valid_receipt_id(self, receipt_id: ReceiptLike) -> bool:
        """Returns whether the given receipt ID is valid and corresponds to an
        active, ongoing asynchronous command.

        Parameters:
            receipt_id: the receipt identifier to test

        Returns:
            True if the given receipt ID is valid and corresponds to an active,
            ongoing asynchronous command, False otherwise
        """
        return self._get_command_from_id(receipt_id) is not None

    def mark_as_clients_notified(self, receipt_id: ReceiptLike) -> None:
        """Marks that the asynchronous command with the given receipt identifier
        was passed back to the client that originally initiated the request.

        Parameters:
            receipt_id: the receipt identifier of the command, or the execution
                status object of the command itself.

        Throws:
            ValueError: if the given receipt belongs to a different manager
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile
            log.warn(f"Expired receipt marked as dispatched: {receipt_id}")
            return

        command.mark_as_clients_notified()

        self._send_finished_signal_if_needed(command)

    async def new(
        self, result: Result, client_to_notify: Optional[str] = None
    ) -> CommandExecutionStatus:
        """Registers the execution of a new asynchronous command in the
        command manager.

        Parameters:
            result: the result to return to the client in the response packet.
                May also be an awaitable object that will eventually provide
                the result of the command.

        Returns:
            CommandExecutionStatus: a newly created CommandExecutionStatus_
                object for the asynchronous command
        """
        receipt = self._builder.create_status_object()
        receipt.mark_as_sent()

        if client_to_notify:
            receipt.add_client_to_notify(client_to_notify)

        self._entries[receipt.id] = receipt
        await self._tx_queue.send((result, receipt))

        return receipt

    async def run(self, cleanup_period: float = 1) -> None:
        """Runs the background tasks related to the command execution
        manager. This method should be launched in a Trio nursery.

        Parameters:
            cleanup_period: number of seconds to wait between consecutive
                cleanups.
        """
        # TODO(ntamas): no need for regular cleanups if we utilize
        # trio.move_on_after() instead
        async with open_nursery() as nursery:
            nursery.start_soon(self._run_cleanup, cleanup_period, name="cleanup_task")
            nursery.start_soon(self._run_execution, nursery, name="executor_task")

    def _cancelled_by_user(self, receipt_id: str) -> None:
        """Marks the asynchronous command with the given receipt identifier
        as having been cancelled by the user.

        Parameters:
            receipt_id: the receipt identifier of the command that timed out
        """
        self._entries.pop(receipt_id, None)

    def _cleanup(self) -> None:
        """Runs a cleanup process on the dictionary containing the commands
        currently in progress, removing items that have expired and those
        for which the command has finished its execution and the result has
        been sent via a ``finished`` signal.

        An item is considered to be expired if it has been created more than
        ``self.timeout`` seconds ago and it has not finished execution yet.
        """
        commands = self._entries
        expiry = time() - self.timeout
        to_remove = [
            key
            for key, status in commands.items()
            if status.finished is not None
            or status.cancelled is not None
            or status.created_at < expiry
        ]
        if to_remove:
            expired = [commands.pop(key) for key in to_remove]
            expired = [command for command in expired if command.is_in_progress]
            self.expired.send(self, statuses=expired)

    def _get_command_from_id(
        self, receipt_id: ReceiptLike
    ) -> Optional[CommandExecutionStatus]:
        """Returns the command execution status object corresponding to the
        given receipt ID, or, if the function is given a
        CommandExecutionStatus_ object, checks whether the object really
        belongs to this manager.

        Parameters:
            receipt_id: the receipt identifier of the command execution status
                object being looked up, or the execution status object of the
                command itself.

        Returns:
            the command execution status object belonging to the given receipt ID.
        """
        if isinstance(receipt_id, CommandExecutionStatus):
            receipt = self._entries.get(receipt_id.id)
            if receipt is not None and receipt != receipt_id:
                raise ValueError("receipt does not belong to this manager")
        else:
            receipt = self._entries.get(receipt_id)
        return receipt

    async def _run_cleanup(self, seconds: float = 1) -> None:
        """Runs the cleanup process periodically in an infinite loop.

        Parameters:
            second: number of seconds to wait between consecutive cleanups.
        """
        async for _ in periodic(seconds):
            try:
                self._cleanup()
            except Exception as ex:
                log.exception(ex)

    async def _run_execution(self, nursery: Nursery) -> None:
        """Runs a task that awaits for the completion of all commands
        that are currently being executed, and updates the corresponding
        command receipts.
        """
        while True:
            result, receipt = await self._rx_queue.receive()
            if isawaitable(result):
                # We need to construct the cancel scope here, not inside
                # self._wait_for, otherwise we would not take into account the
                # time it takes for the nursery to start the execution
                scope = CancelScope(deadline=current_time() + self.timeout)  # type: ignore
                receipt.when_cancelled(scope.cancel)
                nursery.start_soon(
                    self._wait_for,
                    result,
                    receipt.id,
                    scope,
                    name=f"async_operation:{receipt.id}",
                )
            else:
                self._finish(receipt.id, result)

    def _finish(self, receipt_id: ReceiptLike, result: Any = None) -> None:
        """Marks the asynchronous command with the given receipt identifier
        as finished, optionally adding the given object as a result.

        Parameters:
            receipt_id: the receipt identifier of the command that was finished
            result: the result of the command; ``None`` if the command needs no
                response. May also be an instance of an exception; this will be
                handled appropriately.
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile. This should happen
            # only in rare cases as we cancel the awaitables when needed.
            log.warn("Received response for expired receipt: {0}".format(receipt_id))
            return

        command.mark_as_finished(result)
        self._send_finished_signal_if_needed(command)

    def _send_finished_signal_if_needed(self, command: CommandExecutionStatus) -> None:
        """Sends the 'finished' signal for the given command if it has been
        marked as **client notified** (meaning that the clients were notified
        about the corresponding receipt IDs) and as **finished** (meaning that
        the execution of the command has finished and the result object was
        attached to it).
        """
        if command.client_notified and command.finished:
            self.finished.send(self, status=command)

    def _timeout(self, receipt_id: str) -> None:
        """Marks the asynchronous command with the given receipt identifier
        as having timed out.

        Parameters:
            receipt_id: the receipt identifier of the command that timed out
        """
        command = self._entries.pop(receipt_id, None)
        if command:
            self.expired.send(self, statuses=[command])

    async def _wait_for(
        self, awaitable: Awaitable[Any], receipt_id: str, scope: CancelScope
    ) -> None:
        """Waits for the result of the given awaitable and updates the
        command execution receipt with the given ID when the result is
        retrieved from the awaitable.

        Parameters:
            awaitable: the awaitable to wait for
            receipt_id: the receipt corresponding to the awaitable; this receipt
                will be updated when the awaitable finishes
            scope: the cancellation scope that can be used to terminate the
                awaitable earlier
        """
        try:
            with scope:
                result = await awaitable
        except RuntimeError as ex:
            # this is okay, samurai principle
            result = ex
        except TooSlowError as ex:
            # this is okay as well
            result = ex
        except Exception as ex:
            # this might not be okay, let's log it
            log.exception("Unexpected exception caught")
            result = ex

        if scope.cancelled_caught:
            if scope.deadline > current_time():
                self._cancelled_by_user(receipt_id)
            else:
                self._timeout(receipt_id)
        else:
            self._finish(receipt_id, result)
