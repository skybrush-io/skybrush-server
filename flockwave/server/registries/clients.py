"""A registry that contains information about all the clients that the
server is currently connected to.
"""

from __future__ import absolute_import

from blinker import Signal

from ..logger import log as base_log
from ..model import Client
from .base import RegistryBase

__all__ = ("ClientRegistry", )

log = base_log.getChild("registries.clients")


class ClientRegistry(RegistryBase):
    """Registry that contains information about all the clients that the
    server is currently connected to.

    Attributes:
        added (Signal): signal that is sent by the registry when a new client
            has been added to the registry. The signal has a keyword
            argment named ``client`` that contains the client that has just
            been added to the registry.

        count_changed (Signal): signal that is sent by the registry when the
            number of connected clients changed. This can be used by
            extensions to optimize their behaviour when no clients are
            connected.

        removed (Signal): signal that is sent by the registry when a client
            has been removed from the registry. The signal has a keyword
            argument named ``client`` that contains the client that has just
            been removed from the registry.
    """

    added = Signal()
    count_changed = Signal()
    removed = Signal()

    def add(self, client_id, message_hub):
        """Adds a new client to the set of clients connected to the server.

        This function is a no-op if the client is already added. It is
        assumed that a client may not connect twice to the server with the
        same ID.

        Arguments:
            client_id (str): the ID of the client
        """
        if client_id in self:
            return

        client = Client(id=client_id)
        self._entries[client_id] = client
        log.info("Client connected", extra={"id": client_id})

        self.added.send(self, client=client)
        self.count_changed.send(self)

    @property
    def num_entries(self):
        """Returns the number of clients currently connected to the
        server.
        """
        return len(self._entries)

    def remove(self, client_id):
        """Removes a client from the set of clients connected to the server.

        This function is a no-op if the client was already removed.

        Arguments:
            client_id (str): the ID of the client to remove
        """
        try:
            client = self._entries.pop(client_id)
        except KeyError:
            return

        log.info("Client disconnected", extra={"id": client_id})
        self.count_changed.send(self)
        self.removed.send(self, client=client)
