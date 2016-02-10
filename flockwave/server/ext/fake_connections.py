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
from flockwave.server.connection import ConnectionBase
from flockwave.server.model import ConnectionPurpose

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
            connection = FakeConnection()
            name = id_format.format(index)
            self.app.connection_registry.add(
                connection, name=name,
                purpose=ConnectionPurpose.debug
            )


class FakeConnection(ConnectionBase):
    pass


construct = FakeConnectionProviderExtension
