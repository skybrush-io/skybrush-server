"""A registry that contains information about all the different types of
communication channels that the server can handle.

Communication channels may include things like Socket.IO connections, bare
TCP or UDP connections and so on.

Note that the registry keeps track of the different *types* of communication
channels, not each individual channel between a client and the server.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generic, TypeVar

from blinker import Signal

from flockwave.connections import IPAddressAndPort

from ..logger import log as base_log
from ..model import CommunicationChannel
from .base import RegistryBase

__all__ = ("ChannelTypeRegistry",)

log = base_log.getChild("registries.channels")

T = TypeVar("T")


@dataclass(frozen=True)
class ChannelTypeDescriptor(Generic[T]):
    id: str
    """The ID of the communication channel type."""

    factory: Callable[[], CommunicationChannel[T]]
    """A callable that constructs a new communication channel of this type
    when invoked with no arguments.
    """

    broadcaster: Callable[[T], None] | None = None
    """An optional callable that implements broadcasting a message to all clients
    who are currently connected to the server with this communication channel
    type. The callable will be called with the message to be sent as its only
    argument.

    ``None`` means that no such broadcasting is possible and the application
    has to fall back to sending individual messages.
    """

    ssdp_location: Callable[[IPAddressAndPort | None], str | None] | None = None
    """An optional callable that can be called with a single host-port pair or
    ``None`` and that returns a URI describing the location where the communication
    channel can be accessed from the outside. `None` as the input argument means
    a generic query without a specific remote address in mind.
    """

    def get_ssdp_location(self, source: IPAddressAndPort | None = None) -> str | None:
        locator = self.ssdp_location
        return locator(source) if callable(locator) else None


class ChannelTypeRegistry(RegistryBase[ChannelTypeDescriptor], Generic[T]):
    """Registry that contains information about all the communication channel
    types that the server can handle.

    Attributes:
        added (Signal): signal that is sent by the registry when a new
            communication channel type has been registered in the registry.
            The signal has a keyword argment named ``descriptor`` that
            contains information about the channel type that was added.
            ``descriptor`` will be an instance of ChannelTypeDescriptor_.

        count_changed (Signal): signal that is sent by the registry when the
            number of registered communication channel types has changed.

        removed (Signal): signal that is sent by the registry when a
            communication channel type has been removed from the registry.
            The signal has a keyword argment named ``descriptor`` that
            contains information about the channel type that was removed.
            ``descriptor`` will be an instance of ChannelTypeDescriptor_.
    """

    added = Signal()
    count_changed = Signal()
    removed = Signal()

    def add(
        self,
        channel_id: str,
        factory: Callable[[], CommunicationChannel[T]],
        broadcaster: Callable[[T], None] | None = None,
        ssdp_location: Callable[[IPAddressAndPort | None], str | None] | None = None,
    ):
        """Adds a new communication channel class to the registry.

        This function throws an error if the ID is already taken.

        Arguments:
            channel_id: the ID of the communication channel type
            factory: a callable that constructs a new communication channel
                of this type when invoked with no arguments. The callable is
                typically a class that extends CommunicationChannel_ and has an
                appropriate constructor, but can be an arbitrary callable as
                long as it returns an instance of CommunicationChannel_.
            address: a callable that can be called with a single host-port pair
                or ``None`` and that returns a tuple consisting of a hostname
                and the corresponding port where the communication channel can
                be accessed from the outside. The callable may be ``None`` or
                may return ``None`` if such a location cannot sensibly be derived.
                The argument of the callable describes the remote address that is
                interested in the location of the channel; the implementation
                should strive to return an IP address that is on the same subnet
                as the remote address.
            broadcaster: a callable that implements broadcasting a message to
                all clients that are currently connected to the server with this
                communication channel type. The callable will be called with the
                message to be sent as its only argument. When this property is
                ``None``, it is assumed that there is no compact way to broadcast
                a message to all the clients who are connected with this
                channel type, and the application will fall back to sending
                individual messages.
            ssdp_location: a callable that can be called with a single host-port
                pair or ``None`` and that returns a URI describing the location
                where the communication channel can be accessed from the outside.
                For instance, a TCP channel may return ``tcp://192.168.1.17:1234``
                there if the server is listening on 192.168.1.17, port 1234. The
                callable may be ``None`` or may return ``None`` if such a
                location cannot sensibly be derived. The argument of the
                callable describes the remote address that is interested in the
                channel; the implementation should strive to return an IP
                address that is on the same subnet as the remote address.
        """
        if channel_id in self:
            return

        descriptor = ChannelTypeDescriptor(
            id=channel_id,
            factory=factory,
            broadcaster=broadcaster,
            ssdp_location=ssdp_location,
        )
        self._entries[channel_id] = descriptor

        log.debug("Channel registered", extra={"id": channel_id})

        self.added.send(self, id=channel_id, descriptor=descriptor)
        self.count_changed.send(self)

    def create_channel_for(self, channel_id: str) -> CommunicationChannel[T]:
        """Creates a new communication channel with the type whose ID is
        given in the first argument.

        Arguments:
            channel_id (str): the ID of the communication channel type

        Returns:
            CommunicationChannel: a new communication channel of the given
                type.
        """
        result = self._entries[channel_id].factory()
        assert isinstance(result, CommunicationChannel), (
            "communication channel factory did not return a CommunicationChannel"
        )
        return result

    @property
    def num_entries(self):
        """Returns the number of channel types currently registered in the
        registry.
        """
        return len(self._entries)

    def remove(self, channel_id) -> None:
        """Removes a communication channel class by ID from the set of
        channels registered in the registry.

        This function is a no-op if the channel class was already removed.

        The behaviour of the server is undefined if there are still clients
        who use a communication channel of this type.

        Arguments:
            channel_id (str): the ID of the channel type to remove
        """
        try:
            descriptor = self._entries.pop(channel_id)
        except KeyError:
            return

        log.debug("Channel deregistered", extra={"id": channel_id})
        self.count_changed.send(self)
        self.removed.send(self, id=channel_id, descriptor=descriptor)

    @contextmanager
    def use(
        self,
        name: str,
        *,
        factory: Callable[[], CommunicationChannel[T]],
        broadcaster: Callable[[T], None] | None = None,
        ssdp_location: Callable[[IPAddressAndPort | None], str | None] | None = None,
    ) -> Iterator[None]:
        """Context manager that temporarily adds a channel to the channel
        registry and unregisters it upon exiting the context.

        Keyword arguments are forwarded intact to `add()`. See the list of
        supported arguments there.

        Parameters:
            name: name of the channel to register
        """
        try:
            self.add(
                name,
                factory=factory,
                broadcaster=broadcaster,
                ssdp_location=ssdp_location,
            )
            yield
        finally:
            self.remove(name)
