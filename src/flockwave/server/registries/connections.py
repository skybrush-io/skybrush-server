"""A registry that contains information about all the connections that the
server knows.
"""

from __future__ import absolute_import

from blinker import Signal
from contextlib import contextmanager

from ..logger import log as base_log
from ..model import ConnectionInfo, ConnectionPurpose
from .base import RegistryBase

__all__ = ("ConnectionRegistry",)

log = base_log.getChild("registries.connections")


class ConnectionRegistry(RegistryBase):
    """Registry that contains information about all the connections to
    external data sources that are managed by the server.

    The registry allows us to quickly retrieve information about a
    connection by its identifier.
    """

    added = Signal(
        doc="""\
        Signal sent whenever a new connection is added to the registry.

        Parameters:
            entry (ConnectionRegistryEntry): the connection entry that was added
        """
    )
    removed = Signal(
        doc="""\
        Signal sent whenever a connection was removed from the registry.

        Parameters:
            entry (ConnectionRegistryEntry): the connection entry that was removed
        """
    )
    connection_state_changed = Signal(
        doc="""\
        Signal sent whenever the state of a connection in the registry
        changes.

        Parameters:
            entry (ConnectionRegistryEntry): the connection entry whose state
                changed
            new_state (str): the new state
            old_state (str): the old state
        """
    )

    def add(self, connection, name, description=None, purpose=ConnectionPurpose.other):
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
            raise KeyError(
                "another connection is already registered "
                "with this name: {0!r}".format(name)
            )

        purpose = purpose if purpose is not None else ConnectionPurpose.other

        entry = self._create_entry(connection, name)
        entry.purpose = purpose
        if description is not None:
            entry.description = description

        self._entries[name] = entry
        self.added.send(self, entry=entry)

        return entry

    def remove(self, name):
        """Removes an entry from the set of connections.

        This function is a no-op if there is no such connection.

        Arguments:
            name (str): the name of the connection to remove

        Returns:
            the entry that was deregistered, or ``None`` if no connection was
            registered with the given name
        """
        entry = self._entries.pop(name, None)
        if entry is not None:
            self.removed.send(self, entry=entry)
        return entry

    @contextmanager
    def use(self, connection, name, *args, **kwds):
        """Temporarily adds a new connection with the given name and
        additional paramters, hands control back to the caller in a
        context, and then removes the connection when the caller exits
        the context.

        Additional positional and keyword arguments are passed on intact
        to `add()`.

        Yields:
            Connection: the connection registry entry that was added
        """
        if name in self:
            yield self[name]
        else:
            entry = self.add(connection, name, *args, **kwds)

            try:
                yield entry
            finally:
                self.remove(name)

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
        return ConnectionRegistryEntry(self, connection, name)

    def _on_connection_state_changed(self, entry, old_state, new_state):
        """Handler that is called when the state of a connection changes."""
        log.debug(f"Connection {entry.id}: {old_state} --> {new_state}")
        self.connection_state_changed.send(
            self, entry=entry, old_state=old_state, new_state=new_state
        )


class ConnectionRegistryEntry:
    """A single entry in the connection registry."""

    def __init__(self, registry, connection=None, name=None):
        self._connection = None
        self._registry = registry

        self.info = ConnectionInfo(id=name)
        self.connection = connection

    @property
    def connection(self):
        """The connection stored in this entry."""
        return self._connection

    @connection.setter
    def connection(self, value):
        if value == self._connection:
            return

        if self._connection is not None:
            self._connection.state_changed.disconnect(
                self._on_connection_state_changed, sender=self._connection
            )

        self._connection = value

        if self._connection is not None:
            self.info.update_status_from(self._connection)
            self._connection.state_changed.connect(
                self._on_connection_state_changed, sender=self._connection
            )

    @property
    def description(self):
        """The description of the connection; proxied to the info object."""
        return self.info.description

    @description.setter
    def description(self, value):
        self.info.description = value

    @property
    def id(self):
        """The ID of the connection; proxied to the info object."""
        return self.info.id

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

    def _on_connection_state_changed(self, sender, old_state, new_state):
        """Handler that is called when the state of a connection changes."""
        self.info.update_status_from(self._connection)
        self.info.update_timestamp()
        self._registry._on_connection_state_changed(
            entry=self, old_state=old_state, new_state=new_state
        )
