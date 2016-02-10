"""A registry that contains information about all the connections that the
server knows.
"""

from __future__ import absolute_import

from .model import ConnectionInfo, ConnectionPurpose, RegistryBase

__all__ = ("ConnectionRegistry", )


class ConnectionRegistry(RegistryBase):
    """Registry that contains information about all the connections to
    external data sources that are managed by the server.

    The registry allows us to quickly retrieve information about a
    connection by its identifier.
    """

    def add(self, connection, name, description=None,
            purpose=ConnectionPurpose.other):
        """Adds a connection with the given name to the registry.

        Parameters:
            connection (Connection): the connection to add
            name (str): the name of the connection to use in the
                registry.
            description (str or None): a longer, human-readable description
                of the connection. ``None`` means no description.
            purpose (ConnectionPurpose): the purpose of the connection

        Returns:
            ConnectionRegistryEntry: the entry in the registry that was
                created to hold information about the connection

        Throws:
            KeyError: if the given name is already taken
        """
        if name in self:
            raise KeyError("another connection is already registered "
                           "with this name: {0!r}".format(name))

        purpose = purpose if purpose is not None else ConnectionPurpose.other

        entry = self._create_entry(connection, name)
        entry.purpose = purpose
        if description is not None:
            entry.description = description

        self._entries[name] = entry
        return entry

    def _create_entry(self, connection, name):
        """Creates a new entry for the given connection with the given
        name.

        It can safely be assumed that the name is not used yet in the
        registry.

        Parameters:
            connection (Connection): the connection to add
            name (str): the name of the connection to use in the
                registry.

        Returns:
            ConnectionRegistryEntry: the entry in the registry that was
                created to hold information about the connection
        """
        return ConnectionRegistryEntry(connection, name)


class ConnectionRegistryEntry(object):
    """A single entry in the connection registry."""

    def __init__(self, connection=None, name=None):
        self._connection = None
        self.connection = connection
        self.info = ConnectionInfo(id=name)

    @property
    def connection(self):
        """The connection stored in this entry."""
        return self._connection

    @connection.setter
    def connection(self, value):
        if value == self._connection:
            return

        self._connection = value

    @property
    def description(self):
        """The description of the connection; proxied to the info object."""
        return self.info.description

    @description.setter
    def description(self, value):
        self.info.description = value

    @property
    def json(self):
        """Returns the JSON representation of the entry."""
        return self.info.json

    @property
    def purpose(self):
        """The purpose of the connection; proxied to the info object."""
        return self.info.purpose

    @purpose.setter
    def purpose(self, value):
        self.info.purpose = value
