"""Base classes for implementing communication managers that facilitate
communication between UAVs and a ground station via some communication
link (e.g., standard 802.11 wifi).
"""

from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from logging import Logger
from trio import open_memory_channel, WouldBlock
from trio_util import wait_all
from typing import Callable, Generator, Generic, Tuple, TypeVar

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

    @dataclass
    class Entry:
        """A single entry in the communication manager that contains a connection
        managed by the manager and its associated data.
        """

        connection: Connection
        name: str
        can_send: bool = True

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

    def add(self, connection, *, name: str, can_send: bool = True):
        """Adds the given connection to the list of connections managed by
        the communication manager.

        Parameters:
            connection: the connection to add
            name: the name of the connection; passed back to consumers of the
                incoming packet queue along with the received packets so they
                know which connection the packet was received from
            can_send: whether the channel can be used for sending messages
        """
        if self._running:
            raise RuntimeError("cannot add new connections when the manager is running")

        entry = self.Entry(connection, name=name, can_send=bool(can_send))
        self._entries_by_name[name].append(entry)

    def enqueue_packet(self, packet: PacketType, destination: Tuple[str, AddressType]):
        """Requests the communication manager to send the given message packet
        to the given destination and return immediately.

        The packet may be dropped if the outbound queue is currently full.

        Parameters:
            packet: the packet to send
            destination: the name of the communication channel and the address
                on that communication channel to send the packet to.
        """
        queue = self._outbound_tx_queue
        if not queue:
            raise RuntimeError(f"Outbound message queue is closed")

        try:
            queue.send_nowait((packet, destination))
        except WouldBlock:
            if self.log:
                self.log.warn(
                    "Dropping outbound packet; outbound message queue is full"
                )

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
        self, packet: PacketType, destination: Tuple[str, AddressType]
    ):
        """Requests the communication manager to send the given message packet
        to the given destination.

        Blocks until the packet is enqueued in the outbound queue, allowing
        other tasks to run.

        Parameters:
            packet: the packet to send
            destination: the name of the communication channel and the address
                on that communication channel to send the packet to.
        """
        queue = self._outbound_tx_queue
        if not queue:
            raise RuntimeError(f"Outbound message queue is closed")

        await queue.send((packet, destination))

    def _iter_entries(self) -> Generator["Entry", None, None]:
        for _, entries in self._entries_by_name.items():
            yield from entries

    async def _run(self, *, consumer, supervisor):
        tx_queue, rx_queue = open_memory_channel(0)

        tasks = [
            partial(
                supervisor,
                entry.connection,
                task=partial(self._run_inbound_link, entry=entry, queue=tx_queue),
            )
            for entry in self._iter_entries()
        ]
        tasks.append(partial(consumer, rx_queue))
        tasks.append(partial(self._run_outbound_links))

        async with tx_queue, rx_queue:
            await wait_all(*tasks)

    async def _run_inbound_link(self, connection, *, entry, queue):
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

    async def _run_outbound_links(self):
        tx_queue, rx_queue = open_memory_channel(32)
        async with tx_queue, rx_queue:
            try:
                self._outbound_tx_queue = tx_queue
                await self._run_outbound_links_inner(rx_queue)
            finally:
                self._outbound_tx_queue = None

    async def _run_outbound_links_inner(self, queue):
        # TODO(ntamas): a slow outbound link may block sending messages on other
        # outbound links; revise if this causes a problem
        async for message, destination in queue:
            name, address = destination
            entries = self._entries_by_name.get(name)
            sent = False

            if entries:
                for index, entry in enumerate(entries):
                    if entry.channel is not None and entry.can_send:
                        try:
                            await entry.channel.send((message, address))
                            sent = True
                            break
                        except Exception:
                            self.log.exception(
                                f"Error while sending message on channel {name}[{index}]"
                            )

            if not sent:
                if entries:
                    self.log.error(
                        f"Dropping outbound message, all channels broken for: {name!r}"
                    )
                else:
                    self.log.error(
                        f"Dropping outbound message, no such channel: {name!r}"
                    )
