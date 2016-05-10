"""A registry that contains information about all the clients that the
server is currently connected to.
"""

from __future__ import absolute_import

from blinker import Signal

from ..logger import log as base_log
from .base import RegistryBase

__all__ = ("ClientRegistry", )

log = base_log.getChild("registries.client")


class ClientRegistry(RegistryBase):
    """Registry that contains information about all the clients that the
    server is currently connected to.

    Attributes:
        count_changed (Signal): signal that is sent by the registry when the
            number of connected clients changed. This can be used by
            extensions to optimize their behaviour when no clients are
            connected.
    """

    count_changed = Signal()

    def add(self, client_id):
        """Adds a new client to the set of clients connected to the server.

        This function is a no-op if the client is already added. It is
        assumed that a client may not connect twice to the server with the
        same ID.

        Arguments:
            client_id (str): the ID of the client
        """
        if client_id in self:
            return

        self._entries[client_id] = True
        log.info("Client connected", extra={"id": client_id})

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
            del self._entries[client_id]
        except KeyError:
            return

        log.info("Client disconnected", extra={"id": client_id})
        self.count_changed.send(self)
