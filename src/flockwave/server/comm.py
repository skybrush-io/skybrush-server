"""Base classes for implementing communication managers that facilitate
communication between UAVs and a ground station via some communication
link (e.g., standard 802.11 wifi).
"""

import attr

from collections import defaultdict
from functools import partial
from logging import Logger
from trio import open_memory_channel
from trio_util import wait_all
from typing import Callable, Generator, Generic, Optional, Tuple, TypeVar

from flockwave.channels import MessageChannel
from flockwave.connections import Connection


__all__ = ("CommunicationManager",)


#: Type variable representing the type of addresses used by a CommunicationManager
AddressType = TypeVar("AddressType")

#: Type variable representing the type of packets handled by a CommunicationManager
PacketType = TypeVar("PacketType")


class CommunicationManager(Generic[PacketType, AddressType]):
    """Reusable communication manager class for drone driver extensions, with
    multiple responsibilities:

    - watches a set of connections and uses the app supervisor to keep them
      open

    - parses the incoming messages from each of the connections in separate
      tasks, and forwards them to a central queue

    - provides a method that can be used to send a message on any of the
      currently open connections

    Attributes:
        channel_factory: a callable that takes a Connection_ instance and a
            logger object, and that constructs a MessageChannel_ object that
            reads messages from and writes messages to the given connection,
            using the given logger for logging parsing errors
        format_address: a callable that takes an address used by this
            communication manager and formats it into a string so it can be
            used in log messages. Defaults to `str()`.
    """

    channel_factory: Callable[[Connection, Logger], MessageChannel]
    format_address: Callable[[AddressType], str]

    @attr.s
    class Entry:
        """A single entry in the communication manager that contains a connection
        managed by the manager and its associated data.
        """

        connection: Connection = attr.ib()
        name: str = attr.ib()

    def __init__(
        self,
        channel_factory: Callable[[Connection, Logger], MessageChannel],
        format_address: Callable[[AddressType], str] = str,
    ):
        """Constructor.

        Parameters:
            channel_factory: a callable that can be invoked with a connection
                object and a logger instance and that creates a new message
                channel instance that reads messages from and writes messages
                to the given connection
        """
        self.channel_factory = channel_factory
        self.format_address = format_address

        self._entries_by_name = defaultdict(list)
        self._running = False

    def add(self, connection, *, name):
        """Adds the given connection to the list of connections managed by
        the communication manager.

        Parameters:
            connection: the connection to add
            name: the name of the connection; passed back to consumers of the
                incoming packet queue along with the received packets so they
                know which connection the packet was received from
        """
        if self._running:
            raise RuntimeError("cannot add new connections when the manager is running")

        entry = self.Entry(connection, name=name)

        self._entries_by_name[name].append(entry)

    async def run(self, *, consumer, supervisor, log):
        """Runs the communication manager in a separate task, using the
        given supervisor function to ensure that the connections associated to
        the communication manager stay open.

        Parameters:
            consumer: a callable that will be called with a Trio ReceiveChannel_
                that will yield all the packets that are received on any of
                the managed connections. More precisely, the channel will yield
                pairs consisting of a connection name (used when they were
                registered) and another pair holding the received message and
                the address it was received from.
            supervisor: a callable that will be called with a connection
                instance and a `task` keyword argument that represents an
                async callable that will be called whenever the connection is
                opened. This signature matches the `supervise()` method of
                the application instance so you typically want to pass that
                in here.
            log: logger that will be used to log messages from the
                communication manager
        """
        try:
            self._running = True
            self.log = log
            await self._run(consumer=consumer, supervisor=supervisor)
        finally:
            self.log = None
            self._running = False

    async def send_packet(
        self, packet: PacketType, destination: Tuple[str, Optional[AddressType]]
    ):
        """Requests the communication manager to send the given FlockCtrl packet
        to the given destination.

        Parameters:
            packet: the packet to send
            destination: the name of the communication channel and the address
                on that communication channel to send the packet to. `None` as
                an address means to send a broadcast packet on the given
                channel.
        """
        name, address = destination
        entries = self._entries_by_name.get(name)
        if not entries:
            raise ValueError(f"unknown communication channel: {name}")

        await entries[0].channel.send((packet, address))

    def _iter_entries(self) -> Generator["Entry", None, None]:
        for _, entries in self._entries_by_name.items():
            yield from entries

    async def _run(self, *, consumer, supervisor):
        tx_queue, rx_queue = open_memory_channel(0)
        tasks = [
            partial(
                supervisor,
                entry.connection,
                task=partial(self._run_link, entry=entry, queue=tx_queue),
            )
            for entry in self._iter_entries()
        ]
        tasks.append(partial(consumer, rx_queue))

        async with tx_queue, rx_queue:
            await wait_all(*tasks)

    async def _run_link(self, connection, *, entry, queue):
        address = getattr(connection, "address")
        address = self.format_address(address) if address else None
        has_error = False

        try:
            if address:
                self.log.info(f"Connection at {address} up and running.")

            entry.channel = self.channel_factory(connection, self.log)
            async for message in entry.channel:
                await queue.send((entry.name, message))

        except Exception as ex:
            has_error = True
            self.log.exception(ex)
            if address:
                self.log.warn(f"Connection at {address} down, trying to reopen.")

        finally:
            entry.channel = None
            if address and not has_error:
                self.log.info(f"Connection at {address} closed.")
