"""Classes related to the asynchronous execution of commands on
remote UAVs.
"""

from blinker import Signal
from inspect import isasyncgen, isawaitable
from trio import (
    current_time,
    open_memory_channel,
    open_nursery,
    Nursery,
    TooSlowError,
)
from trio.abc import ReceiveChannel, SendChannel
from trio_util import periodic
from typing import (
    cast,
    Any,
    AsyncGenerator,
    Awaitable,
    Optional,
    Union,
    TypeVar,
)

from flockwave.concurrency import aclosing

from .logger import log as base_log
from .model.builders import CommandExecutionStatusBuilder
from .model.commands import CommandExecutionStatus, Progress, Suspend
from .registries.base import RegistryBase

__all__ = ("CommandExecutionManager",)

log = base_log.getChild("commands")


ReceiptLike = Union[CommandExecutionStatus, str]
Result = Union[Any, Awaitable[Any], AsyncGenerator[Any, Any]]

T = TypeVar("T")


class CommandExecutionManager(RegistryBase[CommandExecutionStatus]):
    """Manager that is responsible for keeping track of commands that are
    currently being executed on remote UAVs.

    The manager provides the following functionality:

      - Creates CommandExecutionStatus_ objects and ensures the uniqueness
        of their identifiers.

      - Detects when a command execution has timed out and cleans up the
        list of pending CommandExecutionStatus_ objects periodically.
    """

    cancelled = Signal()
    """Signal that is emitted when an asynchronous command has been cancelled
    by the user. The signal conveys the status object that corresponds to
    the cancelled command.
    """

    expired = Signal()
    """Signal that is emitted when one or more asynchronous commands have timed
    out and the corresponding status objects are now considered as expired. The
    signal conveys the *list* of status objects that have expired.
    """

    finished = Signal()
    """Signal that is emitted when the execution of an asynchronous command
    finishes. The signal conveys the status object that corresponds to the
    finished command.
    """

    progress_updated = Signal()
    """Signal that is emitted when the progress information of the execution of
    an asynchronous command is updated.
    """

    resumed = Signal()
    """Signal that is emitted when the execution of a suspended asynchronous
    command was resumed.
    """

    suspended = Signal()
    """Signal that is emitted when the execution of an asynchronous command was
    suspended.
    """

    timeout: float
    """The number of seconds that must pass since the start of a command to
    consider the command as having timed out.
    """

    _tx_queue: SendChannel[tuple[Result, CommandExecutionStatus]]
    _rx_queue: ReceiveChannel[tuple[Result, CommandExecutionStatus]]

    def __init__(self, timeout: float = 30):
        """Constructor.

        Parameters:
            timeout: the number of seconds that must pass since the start of a
                command to consider the command as having timed out.
        """
        super().__init__()
        self._builder = CommandExecutionStatusBuilder()
        self._tx_queue, self._rx_queue = open_memory_channel(256)
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
            log.warning(
                "Received cancellation request for non-existent receipt: "
                "{0}".format(receipt_id)
            )
            return

        if command.mark_as_cancelled(by_user=True):
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

    def mark_as_clients_notified(self, receipt_id: ReceiptLike, result: Result) -> None:
        """Marks that the asynchronous command with the given receipt identifier
        was passed back to the client that originally initiated the request,
        and forwards the command to the execution task.

        Parameters:
            receipt_id: the receipt identifier of the command, or the execution
                status object of the command itself.
            result: the result to return to the client in the response packet.
                May also be an awaitable object that will eventually provide
                the result of the command.

        Throws:
            ValueError: if the given receipt belongs to a different manager
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request probably expired in the meanwhile
            log.warning(f"Expired receipt marked as dispatched: {receipt_id}")
            return

        command.mark_as_clients_notified()
        self._tx_queue.send_nowait((result, command))  # type: ignore

    def new(self, client_to_notify: Optional[str] = None) -> CommandExecutionStatus:
        """Registers the execution of a new asynchronous command in the
        command manager.

        Note that the execution of the command will not start yet; it is up to
        the caller to mark it as ready for execution when the ID of the
        receipt in the status object has been sent back to the client.

        Returns:
            a newly created CommandExecutionStatus_ object for the asynchronous
            command
        """
        status = self._builder.create_status_object()
        status.mark_as_sent()
        status.set_deadline_from_now(self.timeout)

        if client_to_notify:
            status.add_client_to_notify(client_to_notify)

        # status object gets stored in self._entries here. Execution will
        # continue in the task that receives the (result, status) tuple and
        # that task will be responsible for removing it.
        self._entries[status.id] = status
        return status

    def resume(self, receipt_id: ReceiptLike, value: Any) -> None:
        """Resume the execution of a suspended asynchronous command with the
        given receipt ID.

        Parameters:
            receipt_id: the receipt identifier of the command that should be
                resumed
            value: optional value to pass to the suspended command
        """
        command = self._get_command_from_id(receipt_id)
        if command is None:
            # Request has probably expired in the meanwhile
            log.warning(
                "Received resume request for non-existent receipt: "
                "{0}".format(receipt_id)
            )
            return

        if command.mark_as_resumed(value):
            self.resumed.send(self, status=command)

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

    def _cleanup(self) -> None:
        """Runs a cleanup process on the dictionary containing the commands
        currently in progress, removing items that have expired and those
        for which the command has finished its execution and the result has
        been sent via a ``finished`` signal.

        An item is considered to be expired if it has been created more than
        ``self.timeout`` seconds ago and it has not finished execution yet.
        """
        commands = self._entries
        now = current_time()
        to_remove = [
            key
            for key, status in commands.items()
            if not status.is_in_progress or status.is_expired(now)
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
            status = self._entries.get(receipt_id.id)
            if status is not None and status != receipt_id:
                raise ValueError("receipt does not belong to this manager")
        else:
            status = self._entries.get(receipt_id)
        return status

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
            result, status = await self._rx_queue.receive()

            # At this point, the status object should still be in
            # self._entries. We will remove it either now (if result is
            # not async), or at the end of the self._wait_for() task.

            if isawaitable(result) or isasyncgen(result):
                # Function returned an awaitable or an async generator, so we
                # will receive a result at some unspecified point in the
                # future. We need to set up a timeout and wait for it.
                #
                # We need to construct the cancel scope here, not inside
                # self._wait_for, otherwise we would not take into account the
                # time it takes for the nursery to start the execution
                nursery.start_soon(
                    self._wait_for,
                    result,
                    status,
                    name=f"async_operation:{status.id}",
                )
            else:
                status.mark_as_finished(result)
                self._finish(status)

    def _finish(self, status: CommandExecutionStatus) -> None:
        """Finishes the lifecycle of the given status object, removing it from
        the ``_entries`` map. Dispatches appropriate signals depending on the
        final state of the object.

        Parameters:
            status: the command execution status object
        """
        if status.id not in self._entries:
            # Request has probably expired in the meanwhile. This should happen
            # only in rare cases as we cancel the awaitables when needed.
            log.warning(f"Execution of task finished with expired receipt: {status.id}")
            return

        del self._entries[status.id]

        # Check whether the cancel scope of the command was cancelled
        if status._cancel_scope.cancelled_caught:
            if status.cancelled is not None:
                # Cancellation came from the user, we have already added a
                # timestamp and sent the cancelled signal. Nothing to do now.
                pass
            else:
                # Cancellation due to timeout; remember the current timestamp as
                # it was not set yet, and send a cancelled signal
                status.mark_as_cancelled(by_user=False)
                self._send_cancelled_signal_if_needed(status)
        else:
            self._send_finished_signal_if_needed(status)

    def _send_cancelled_signal_if_needed(self, command: CommandExecutionStatus) -> None:
        """Sends the 'cancelled' signal for the given command if it has been
        marked as **client notified** (meaning that the clients were notified
        about the corresponding receipt IDs) and as **cancelled** (meaning that
        the execution of the command was cancelled.
        """
        if command.client_notified and command.cancelled:
            self.expired.send(self, statuses=[command])

    def _send_finished_signal_if_needed(self, command: CommandExecutionStatus) -> None:
        """Sends the 'finished' signal for the given command if it has been
        marked as **client notified** (meaning that the clients were notified
        about the corresponding receipt IDs) and as **finished** (meaning that
        the execution of the command has finished and the result object was
        attached to it).
        """
        if command.client_notified and command.finished:
            self.finished.send(self, status=command)

    async def _wait_for(
        self,
        async_obj: Union[Awaitable[Any], AsyncGenerator[Any, Any]],
        status: CommandExecutionStatus,
    ) -> None:
        try:
            with status._cancel_scope:
                try:
                    if isawaitable(async_obj):
                        result = await async_obj
                    else:
                        result = await self._wait_for_generator(
                            cast(AsyncGenerator[Any, Any], async_obj), status
                        )
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

                # We can get here only if the user did not cancel the execution and
                # we have not timed out either. In this case we can safely mark the
                # command as finished
                status.mark_as_finished(result)

        finally:
            self._finish(status)

    async def _wait_for_generator(
        self, it: AsyncGenerator[T, Any], status: CommandExecutionStatus
    ) -> Union[Optional[T], Exception]:
        """Waits for yielded progress updates from an async iterator and updates
        the command execution receipt with the progress information and the
        returned result from the iterator.

        Parameters:
            it: the async iterator to process
            status: the status object that is to be manipulated when the
                async generator finishes or the execution times out
        """
        result: Optional[T] = None

        async with aclosing(it) as gen:
            data_from_client: Any = None
            while True:
                try:
                    item = await gen.asend(data_from_client)
                except StopAsyncIteration:
                    break

                data_from_client = None

                if isinstance(item, Suspend):
                    # Operation requested suspension
                    with status.suspended(post_timeout=self.timeout) as future:
                        if status.progress is not None:
                            status.progress.message = item.message
                            status.progress.object = item.object
                        else:
                            status.progress = item.to_progress()
                        self.suspended.send(self, status=status)
                        data_from_client = await future.wait()
                elif isinstance(item, Progress):
                    # Operation posted a progress report
                    status.set_deadline_from_now(self.timeout)
                    status.progress = item
                    self.progress_updated.send(self, status=status)
                else:
                    # Operation returned a result
                    result = item

        return result
