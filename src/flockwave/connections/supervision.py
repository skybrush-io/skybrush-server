import attr

from functools import partial
from trio import CancelScope, open_memory_channel, open_nursery, sleep
from trio_util import wait_any
from typing import Awaitable, Callable, Optional

from .base import Connection


__all__ = (
    "ConnectionSupervisor",
    "ConnectionTask",
    "supervise",
    "constant_delay_policy",
    "default_policy",
    "no_reconnection_policy",
)


SupervisionPolicy = Callable[[Connection, Exception], Optional[float]]
ConnectionTask = Callable[[Connection], Awaitable[None]]


class ConnectionSupervisor:
    """Connection supervisor object that supervises a set of connections and
    attempts to ensure that each connection remains open.

    This object is a more complex version of the `supervise()` function that is
    able to handle multiple connections at the same time.

    See `supervise()` for more details about the supervision policy and how
    the supervision works in general.
    """

    @attr.s
    class Entry:
        policy: SupervisionPolicy = attr.ib()
        cancel_scope: CancelScope = attr.ib(default=None)
        task: Optional[ConnectionTask] = attr.ib(default=None)

        def cancel(self):
            self.cancel_scope.cancel()

    def __init__(self, policy: Optional[SupervisionPolicy] = None):
        """Constructor.

        Parameters:
            policy: the supervision policy to use; defaults to a constant
                delay of one second between reconnection attempts
        """
        self._policy = policy or default_policy

        self._entries = {}
        self._nursery = None

        self._tx_queue, self._rx_queue = open_memory_channel(32)

    async def add(
        self,
        connection: Connection,
        *,
        task: ConnectionTask = None,
        policy: Optional[SupervisionPolicy] = None,
    ):
        """Adds a connection to supervise.

        Parameters:
            connection: the connection to supervise
            task: optional async callable that will be called with the connection
                as its only argument after it is opened
            policy: the supervision policy to use; defaults to the default
                supervision policy of the supervisor.
        """
        await self._tx_queue.send(("add", (connection, task, policy)))

    async def remove(self, connection: Connection) -> None:
        """Removes a supervised connection.

        This function will also close the connection immediately.
        """
        await self._tx_queue.send(("remove", (connection,)))

    async def run(self):
        """Main loop of the connection supervisor. Must be executed in a Trio
        nursery.
        """
        async with open_nursery() as nursery:
            self._nursery = nursery
            try:
                await self._run_main_loop()
            finally:
                self._nursery = None

    async def _run_main_loop(self):
        while True:
            command, args = await self._rx_queue.receive()

            if command == "add":
                connection, task, policy = args
                self._nursery.start_soon(self.supervise, connection, task, policy)
            elif command == "remove":
                connection, = args
                entry = self._entries.get(connection)
                if entry is not None:
                    entry.cancel()

    async def _close(self, connection):
        """Closes the given connection and stops monitoring it.

        Returns when the connection was closed successfully.
        """
        connection.disconnected.disconnect(
            self._on_connection_disconnected, sender=connection
        )
        await connection.close()

    async def supervise(
        self, connection: Connection, task: ConnectionTask, policy: SupervisionPolicy
    ) -> None:
        """Opens the given connection and supervises it such that it is
        reopened when the connection is connected.

        Optionally spawns a task when the connection is connected. The task
        will get the connection as its first and only argument.
        """
        assert connection not in self._entries

        policy = policy or self._policy

        with CancelScope() as scope:
            self._entries[connection] = self.Entry(cancel_scope=scope, policy=policy)
            try:
                await supervise(connection, task=task, policy=self._policy)
            finally:
                del self._entries[connection]


async def supervise(
    connection: Connection,
    *,
    task: Optional[ConnectionTask] = None,
    policy: Optional[SupervisionPolicy] = None,
):
    """Asynchronous function that opens a connection when entered, and tries to
    keep it open until the function itself is cancelled.

    When an exception happens while the connection is open or being opened, the
    context manager will forward the connection object that threw an exception
    and the exception itself to a designated _supervision policy_, which
    should then return what to do. The policy must return one of the following:

    * `False` or `None` to close the connection without raising an exception,

    * an integer or floating-point number to ignore the exception and attempt a
      reconnection after a delay (expressed in seconds).

    Exceptions raised by the policy itself will be propagated upwards and thrown
    from this function as well.

    The policy will also be called with the connection object and `"open"` in
    place of the exception if the connection was established successfully, or
    `"close"` in place of the exception if the associated task terminated or
    the connection closed in a normal manner. Calls with `"open"` as the second
    argument are only informative so the policy can track the time while the
    connection was alive; in this case, the return value of the policy is
    ignored.

    Parameters:
        connection: the connection to supervise
        task: optional async task to execute after the connection is opened.
            The task will receive the connection as its first and only
            argument. It will be cancelled if the connection is closed.
        policy: the supervision policy to use; defaults to a constant
            delay of one second between reconnection attempts
    """
    policy = policy or default_policy

    while True:
        try:
            await connection.open()
            policy(connection, "open")

            disconnection_event = connection.wait_until_disconnected
            if task:
                await wait_any(partial(task, connection), disconnection_event)
            else:
                await disconnection_event()
        except Exception as ex:
            # Connection closed unexpectedly
            action = policy(connection, ex)
        else:
            # Connection closed normally
            action = policy(connection, "close")

        # Handle the action proposed by the supervisor
        if action is False or action is None:
            break
        elif isinstance(action, (int, float)):
            await sleep(action)
        else:
            raise ValueError(f"invalid supervision policy action: {action}")


def _constant(x):
    """Function factory that returns a function that accepts an arbitrary
    number of arguments and always returns the same constant.

    Parameters:
        x (object): the constant to return

    Returns:
        callable: a function that always returns the given constant,
            irrespectively of its input
    """

    def func(*args, **kwds):
        return x

    return func


def constant_delay_policy(seconds):
    """Supervision policy factory that creates a supervision policy that
    waits a given number of seconds before reconnecting.
    """
    return _constant(float(seconds))


default_policy = constant_delay_policy(1)
no_reconnection_policy = _constant(False)
