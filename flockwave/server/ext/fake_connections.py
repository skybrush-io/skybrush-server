"""Extension that creates one or more fake connection objects in
the server.

The fake connections stay alive for a given number of seconds when they
are opened, then they close themselves and refuse to respond to further
opening attempts for a given number of seconds. The length of both time
intervals can be configured.

Useful primarily for debugging purposes.
"""

from __future__ import absolute_import

from .base import ExtensionBase
from eventlet import spawn_after
from eventlet.greenthread import sleep
from flockwave.server.connection import ConnectionBase, ConnectionState, \
    ReconnectionWrapper
from flockwave.server.model import ConnectionPurpose
from six import itervalues
from time import time

__all__ = ()


class FakeConnectionProviderExtension(ExtensionBase):
    """Extension that creates one or more fake connections in the server."""

    def __init__(self):
        """Constructor."""
        super(FakeConnectionProviderExtension, self).__init__()
        self.connections = {}

    def configure(self, configuration):
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "fakeConnection{0}")
        for index in xrange(count):
            connection = ReconnectionWrapper(FakeConnection())
            name = id_format.format(index)
            self.connections[name] = connection
            self.app.connection_registry.add(
                connection, name=name,
                purpose=ConnectionPurpose.debug
            )

    def spindown(self):
        """Stops the connections when the extension spins down."""
        for connection in itervalues(self.connections):
            connection.close()

    def spinup(self):
        """Starts the connections when the extension spins up."""
        for connection in itervalues(self.connections):
            connection.open()


class FakeConnection(ConnectionBase):
    """Fake connection class used by this extension.

    This connection class breaks the connection two seconds after it was
    opened. Subsequent attempts to open the connection will be blocked
    up to at least three seconds after the connection was closed the last
    time.
    """

    def __init__(self):
        """Constructor."""
        super(FakeConnection, self).__init__()
        self._open_disallowed_until = None

    def open(self):
        """Opens the connection if it is currently allowed. Opening the
        connection will start a timer that closes the connection in
        two seconds.
        """
        if self.state in (ConnectionState.CONNECTED,
                          ConnectionState.CONNECTING):
            return

        self._set_state(ConnectionState.CONNECTING)
        if self._open_disallowed_until is not None:
            now = time()
            if now < self._open_disallowed_until:
                sleep(self._open_disallowed_until - now)

        self._set_state(ConnectionState.CONNECTED)
        spawn_after(seconds=2, func=self.close)

    def close(self):
        """Closes the connection and blocks reopening attempts in the next
        three seconds.
        """
        if self.state in (ConnectionState.DISCONNECTED,
                          ConnectionState.DISCONNECTING):
            return

        self._set_state(ConnectionState.DISCONNECTING)
        self._set_state(ConnectionState.DISCONNECTED)
        self._open_disallowed_until = time() + 3


construct = FakeConnectionProviderExtension
