"""Base classes for implementing communication managers that facilitate
communication between UAVs and a ground station via some communication
link (e.g., standard 802.11 wifi).
"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from errno import ENETDOWN, ENETUNREACH
from functools import partial
from logging import Logger
from trio import BrokenResourceError, open_memory_channel, WouldBlock
from trio_util import wait_all
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Generator,
    Generic,
    Iterator,
    List,
    Optional,
    Tuple,
    TypeVar,
)

from flockwave.channels import MessageChannel
from flockwave.connections import Connection

from .types import Disposer


__all__ = ("BROADCAST", "CommunicationManager")


#: Type variable representing the type of addresses used by a CommunicationManager
AddressType = TypeVar("AddressType")

#: Type variable representing the type of packets handled by a CommunicationManager
PacketType = TypeVar("PacketType")

#: Marker object used to denote packets that should be broadcast over a
#: communication channel with no specific destination address
BROADCAST = object()

#: Marker object used to denote "no broadcast address"
NO_BROADCAST_ADDRESS = object()

#: Special Windows error codes for "network unreachable" condition
WSAENETDOWN = 10050
WSAENETUNREACH = 10051
WSAESERVERUNREACH = 10065


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
    log: Logger

    BROADCAST: ClassVar[object]
    """Marker object that is used to indicate that a message is a broadcast
    message.
    """

    @dataclass
    class Entry:
        """A single entry in the communication manager that contains a connection
        managed by the manager, the associated message channel, and a few
        additional properties.

        Each entry is permanently assigned to a connection and has a name that
        uniquely identifies the connection. Besides that, it has an associated
        MessageChannel_ instance that is not `None` if and only if the connection
        is up and running.
        """

        connection: Connection
        name: str
        can_send: bool = True
        channel: Optional[MessageChannel] = None

        @property
        def is_open(self) -> bool:
            """Returns whether the communication channel represented by this
            entry is up and running.
            """
            return self.channel is not None

    _aliases: Dict[str, str]
    _entries_by_name: Dict[str, List[Entry]]

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

        self._aliases = {}
        self._entries_by_name = defaultdict(list)
        self._running = False
        self._outbound_tx_queue = None

    def add(self, connection, *, name: str, can_send: Optional[bool] = None):
        """Adds the given connection to the list of connections managed by
        the communication manager.

        Parameters:
            connection: the connection to add
            name: the name of the connection; passed back to consumers of the
                incoming packet queue along with the received packets so they
                know which connection the packet was received from
            can_send: whether the channel can be used for sending messages.
                `None` means to try figuring it out from the connection object
                itself by querying its `can_send` attribute. If the attribute
                is missing, we assume that the connection _can_ send messages.
        """
        assert connection is not None

        if self._running:
            raise RuntimeError("cannot add new connections when the manager is running")

        if can_send is None:
            can_send = bool(getattr(connection, "can_send", True))

        entry = self.Entry(connection, name=name, can_send=bool(can_send))
        self._entries_by_name[name].append(entry)

    def add_alias(self, alias: str, *, target: str) -> Disposer:
        """Adds the given alias to the connection names recognized by the
        communication manager. Can be used to decide where certain messages
        should be routed to by dynamically assigning the alias to one of the
        "real" connection names.
        """
        self._aliases[alias] = target
        return partial(self.remove_alias, alias)

    async def broadcast_packet(
        self,
        packet: PacketType,
        *,
        destination: Optional[str] = None,
        allow_failure: bool = False,
    ) -> None:
        """Requests the communication manager to broadcast the given message
        packet to all destinations, or to the broadcast address of a single
        destination.

        Blocks until the packet is enqueued in the outbound queue, allowing
        other tasks to run.

        Parameters:
            packet: the packet to send
        """
        queue = self._outbound_tx_queue
        if not queue:
            if not allow_failure:
                raise BrokenResourceError("Outbound message queue is closed")
            else:
                return

        address = BROADCAST if destination is None else (destination, BROADCAST)

        await queue.send((packet, address))

    def enqueue_broadcast_packet(
        self,
        packet: PacketType,
        *,
        destination: Optional[str] = None,
        allow_failure: bool = False,
    ) -> None:
        """Requests the communication manager to broadcast the given message
        packet to all destinations and return immediately.

        The packet may be dropped if the outbound queue is currently full.

        Parameters:
            packet: the packet to send
        """
        queue = self._outbound_tx_queue
        if not queue:
            if not allow_failure:
                raise BrokenResourceError("Outbound message queue is closed")
            else:
                return

        address = BROADCAST if destination is None else (destination, BROADCAST)

        try:
            queue.send_nowait((packet, address))
        except WouldBlock:
            if self.log:
                self.log.warning(
                    "Dropping outbound broadcast packet; outbound message queue is full"
                )

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
            raise BrokenResourceError("Outbound message queue is closed")

        try:
            queue.send_nowait((packet, destination))
        except WouldBlock:
            if self.log:
                self.log.warning(
                    "Dropping outbound packet; outbound message queue is full"
                )

    def is_channel_open(self, name: str) -> bool:
        """Returns whether the channel with the given name is currently up and
        running.
        """
        entries = self._entries_by_name.get(name)
        return any(entry.is_open for entry in entries) if entries else False

    def open_channels(self) -> Iterator[MessageChannel]:
        """Returns an iterator that iterates over the list of open message
        channels corresponding to this network.
        """
        for entries in self._entries_by_name.values():
            for entry in entries:
                if entry.channel:
                    yield entry.channel

    async def run(self, *, consumer, supervisor, log, tasks=None):
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
            tasks: optional list of additional tasks that should be executed
                while the communication manager is managing the messages. Can
                be used to implement heartbeating on the connection channel.
        """
        try:
            self._running = True
            self.log = log
            await self._run(consumer=consumer, supervisor=supervisor, tasks=tasks)
        finally:
            self.log = None  # type: ignore
            self._running = False

    def remove_alias(self, alias: str) -> None:
        """Removes the given alias from the connection aliases recognized by
        the communication manager.
        """
        del self._aliases[alias]

    async def send_packet(
        self, packet: PacketType, destination: Tuple[str, AddressType]
    ) -> None:
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
            raise BrokenResourceError("Outbound message queue is closed")

        await queue.send((packet, destination))

    @contextmanager
    def with_alias(self, alias: str, *, target: str):
        """Context manager that registers an alias when entering the context and
        unregisters it when exiting the context.
        """
        disposer = self.add_alias(alias, target=target)
        try:
            yield
        finally:
            disposer()

    def _iter_entries(self) -> Generator["Entry", None, None]:
        for _, entries in self._entries_by_name.items():
            yield from entries

    async def _run(
        self, *, consumer, supervisor, tasks: List[Callable[..., Awaitable[Any]]]
    ) -> None:
        tx_queue, rx_queue = open_memory_channel(0)

        tasks = [partial(task, self) for task in (tasks or [])]
        tasks.extend(
            partial(
                supervisor,
                entry.connection,
                task=partial(self._run_inbound_link, entry=entry, queue=tx_queue),
            )
            for entry in self._iter_entries()
        )
        tasks.append(partial(consumer, rx_queue))
        tasks.append(self._run_outbound_links)

        async with tx_queue, rx_queue:
            await wait_all(*tasks)

    async def _run_inbound_link(self, connection, *, entry, queue):
        has_error = False
        channel_created = False
        address = None

        log_extra = {"id": entry.name or ""}

        try:
            address = getattr(connection, "address", None)
            address = self.format_address(address) if address else None

            entry.channel = self.channel_factory(connection, self.log)

            channel_created = True
            if address:
                self.log.info(
                    f"Connection at {address} up and running", extra=log_extra
                )
            else:
                self.log.info("Connection up and running", extra=log_extra)

            async with entry.channel:
                async for message in entry.channel:
                    await queue.send((entry.name, message))

        except Exception as ex:
            has_error = True

            if not isinstance(ex, BrokenResourceError):
                self.log.exception(ex)

            if channel_created:
                if address:
                    self.log.warning(
                        f"Connection at {address} down, trying to reopen...",
                        extra=log_extra,
                    )
                else:
                    self.log.warning(
                        "Connection down, trying to reopen...", extra=log_extra
                    )

        finally:
            entry.channel = None
            if channel_created and not has_error:
                if address:
                    self.log.info(f"Connection at {address} closed", extra=log_extra)
                else:
                    self.log.info("Connection closed", extra=log_extra)

    async def _run_outbound_links(self):
        # ephemeris RTK streams send messages in bursts so it's better to have
        # a relatively large queue here
        tx_queue, rx_queue = open_memory_channel(128)
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
            if destination is BROADCAST:
                await self._send_message_to_all_channels(message)
            else:
                await self._send_message_to_single_channel(message, destination)

    async def _send_message_to_all_channels(self, message):
        for entries in self._entries_by_name.values():
            for _index, entry in enumerate(entries):
                channel = entry.channel
                address = getattr(channel, "broadcast_address", NO_BROADCAST_ADDRESS)
                if address is not NO_BROADCAST_ADDRESS:
                    try:
                        await channel.send((message, address))
                    except Exception:
                        # we are going to try all channels so it does not matter
                        # if a few of them fail for whatever reason
                        pass

    async def _send_message_to_single_channel(self, message, destination):
        name, address = destination

        entries = self._entries_by_name.get(name)
        if not entries:
            # try with an alias
            name = self._aliases.get(name)
            entries = self._entries_by_name.get(name)  # type: ignore

        sent = False
        is_broadcast = address is BROADCAST

        if entries:
            for index, entry in enumerate(entries):
                if entry.is_open and entry.can_send:
                    try:
                        if is_broadcast:
                            # This message should be broadcast on this channel;
                            # let's check if the channel has a broadcast address
                            address = getattr(
                                entry.channel,
                                "broadcast_address",
                                NO_BROADCAST_ADDRESS,
                            )
                            is_broadcast = True
                            if address is not NO_BROADCAST_ADDRESS:
                                await entry.channel.send((message, address))
                                sent = True
                        else:
                            await entry.channel.send((message, address))
                            sent = True
                        if sent:
                            break
                    except OSError as ex:
                        if ex.errno in (
                            ENETDOWN,
                            ENETUNREACH,
                            WSAENETDOWN,
                            WSAENETUNREACH,
                            WSAESERVERUNREACH,
                        ):
                            # This is okay
                            self.log.error(
                                "Network is down or unreachable",
                                extra={"id": name or "", "telemetry": "ignore"},
                            )
                        else:
                            self.log.exception(
                                f"Error while sending message on channel {name}[{index}]",
                                extra={"id": name or ""},
                            )
                    except Exception:
                        self.log.exception(
                            f"Error while sending message on channel {name}[{index}]",
                            extra={"id": name or ""},
                        )

        if not sent and not is_broadcast:
            if entries:
                self.log.warning(
                    f"Dropping outbound message, all channels broken for: {name!r}"
                )
            else:
                self.log.warning(
                    f"Dropping outbound message, no such channel: {name!r}"
                )


CommunicationManager.BROADCAST = BROADCAST
