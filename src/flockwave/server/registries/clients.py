"""A registry that contains information about all the clients that the
server is currently connected to.
"""

from blinker import Signal
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, Set

from flockwave.server.model.client import Client
from flockwave.server.registries.channels import ChannelTypeRegistry
from flockwave.server.logger import log as base_log

from .base import RegistryBase

__all__ = ("ClientRegistry",)

log = base_log.getChild("registries.clients")


class ClientRegistry(RegistryBase[Client]):
    """Registry that contains information about all the clients that the
    server is currently connected to.

    Attributes:
        added (Signal): signal that is sent by the registry when a new client
            has been added to the registry. The signal has a keyword
            argment named ``client`` that contains the client that has just
            been added to the registry.

        channel_type_registry (ChannelTypeRegistry): the channel type
            registry that the client registry turns to when it has to
            construct a new communication channel instance to a client

        count_changed (Signal): signal that is sent by the registry when the
            number of connected clients changed. This can be used by
            extensions to optimize their behaviour when no clients are
            connected.

        removed (Signal): signal that is sent by the registry when a client
            has been removed from the registry. The signal has a keyword
            argument named ``client`` that contains the client that has just
            been removed from the registry.
    """

    added: Signal = Signal()
    count_changed: Signal = Signal()
    removed: Signal = Signal()

    channel_type_registry: ChannelTypeRegistry

    _client_id_to_channel_type: Dict[str, str]
    _entries_by_channel_type: Dict[str, Set[str]]

    def __init__(self, channel_type_registry: ChannelTypeRegistry):
        """Constructor."""
        super().__init__()
        self.channel_type_registry = channel_type_registry
        self._client_id_to_channel_type = {}
        self._entries_by_channel_type = defaultdict(set)

    def add(self, client_id: str, channel_type: str) -> Client:
        """Adds a new client to the set of clients connected to the server.

        This function is a no-op if the client is already added. It is
        assumed that a client may not connect twice to the server with the
        same ID.

        Arguments:
            client_id: the ID of the client
            channel_type: the type of the communication channel that
                connects the client to the server. It must be registered in
                the channel type registry.

        Returns:
            the client object that was added
        """
        if client_id in self:
            return self[client_id]

        channel = self.channel_type_registry.create_channel_for(channel_type)
        client = Client(_id=client_id, _channel=channel)
        channel.bind_to(client)

        self._entries[client_id] = client
        self._client_id_to_channel_type[client_id] = channel_type
        self._entries_by_channel_type[channel_type].add(client_id)

        log.info("Client connected", extra={"id": client_id})

        self.added.send(self, client=client)
        self.count_changed.send(self)

        return client

    def client_ids_for_channel_type(self, channel_type: str) -> Iterable[str]:
        """Returns an iterator that contains the IDs of all the clients who
        are registered in the registry with the given channel type.

        Arguments:
            channel_type (str): the communication channel type to query

        Returns:
            Iterator[str]: an iterable that yields the IDs of all the clients
                who are registered in the registry with the given channel type
        """
        return iter(self._entries_by_channel_type.get(channel_type, []))

    def has_clients_for_channel_type(self, channel_type: str) -> bool:
        """Returns whether there is at least one connected client for the
        given channel type.

        Arguments:
            channel_type: the communication channel type to query

        Returns:
            ``True`` if there is at least one connected client with the given
            channel type, ``False`` otherwise
        """
        return bool(self._entries_by_channel_type.get(channel_type))

    @property
    def num_entries(self) -> int:
        """Returns the number of clients currently connected to the
        server.
        """
        return len(self._entries)

    def remove(self, client_id: str) -> None:
        """Removes a client from the set of clients connected to the server.

        This function is a no-op if the client was already removed.

        Arguments:
            client_id: the ID of the client to remove
        """
        try:
            client = self._entries.pop(client_id)
        except KeyError:
            return

        try:
            channel_type = self._client_id_to_channel_type.pop(client_id)
        except KeyError:
            # This should not happen
            log.warn("Cannot find channel type for client ID {0!r}".format(client_id))
            return

        try:
            self._entries_by_channel_type[channel_type].remove(client_id)
        except KeyError:
            # This should not happen
            log.warn(
                "Cannot remove channel type index entry for "
                "client ID {0!r}".format(client_id)
            )

        log.info("Client disconnected", extra={"id": client_id})

        self.count_changed.send(self)
        self.removed.send(self, client=client)

    @contextmanager
    def use(self, client_id: str, channel_type: str) -> Iterator[Client]:
        """Temporarily adds a new client with the given client ID and
        channel type, hands control back to the caller in a context, and
        then removes the client when the caller exits the context.

        Arguments:
            client_id: the ID of the client
            channel_type: the type of the communication channel that
                connects the client to the server. It must be registered in
                the channel type registry.

        Yields:
            Client: the client object that was added
        """
        if client_id in self:
            yield self[client_id]
        else:
            client = self.add(client_id, channel_type)
            try:
                yield client
            finally:
                self.remove(client_id)
