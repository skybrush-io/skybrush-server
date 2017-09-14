"""A registry that contains information about all the different types of
communication channels that the server can handle.

Communication channels may include things like Socket.IO connections, bare
TCP or UDP connections and so on.

Note that the registry keeps track of the different *types* of communication
channels, not each individual channel between a client and the server.
"""

from __future__ import absolute_import

from blinker import Signal

from ..logger import log as base_log
from .base import RegistryBase

__all__ = ("ChannelTypeRegistry", )

log = base_log.getChild("registries.channels")


class ChannelTypeRegistry(RegistryBase):
    """Registry that contains information about all the communication channel
    types that the server can handle.

    Attributes:
        added (Signal): signal that is sent by the registry when a new
            communication channel type has been registered in the registry.
            The signal has a keyword argment named ``factory`` that contains
            the communication channel *class* that has just been added to
            the registry, and a keyword argument named ``id`` that contains
            the unique identifier of the communication channel type.

        count_changed (Signal): signal that is sent by the registry when the
            number of registered communication channel types has changed.

        removed (Signal): signal that is sent by the registry when a
            communication channel type has been removed from the registry.
            The signal has a keyword argment named ``factory`` that contains
            the communication channel *class* that has just been removed from
            the registry, and a keyword argument named ``id`` that contains
            the unique identifier of the communication channel type.
    """

    added = Signal()
    count_changed = Signal()
    removed = Signal()

    def add(self, channel_id, factory):
        """Adds a new communication channel class to the registry.

        This function throws an error if the ID is already taken.

        Arguments:
            channel_id (str): the ID of the communication channel type
            factory (callable): a callable that constructs a new
                communication channel of this type when invoked with no
                arguments. This is typically a class that extends
                CommunicationChannel_, but can be an arbitrary callable as
                long as it returns an instance of CommunicationChannel_.
        """
        if channel_id in self:
            return

        self._entries[channel_id] = factory
        log.info("Channel registered", extra={"id": channel_id})

        self.added.send(self, id=channel_id, factory=factory)
        self.count_changed.send(self)

    @property
    def num_entries(self):
        """Returns the number of channel types currently registered in the
        registry.
        """
        return len(self._entries)

    def remove(self, channel_id):
        """Removes a communication channel class by ID from the set of
        channels registered in the registry.

        This function is a no-op if the channel class was already removed.

        The behaviour of the server is undefined if there are still clients
        who use a communication channel of this type.

        Arguments:
            channel_id (str): the ID of the channel type to remove
        """
        try:
            factory = self._entries.pop(channel_id)
        except KeyError:
            return

        log.info("CHannel deregistered", extra={"id": channel_id})
        self.count_changed.send(self)
        self.removed.send(self, id=channel_id, factory=factory)
