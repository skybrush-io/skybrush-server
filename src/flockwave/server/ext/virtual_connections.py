"""Extension that creates one or more virtual connection objects in
the server.

The virtual connections stay alive for a given number of seconds when they
are opened, then they close themselves and refuse to respond to further
opening attempts for a given number of seconds. The length of both time
intervals can be configured.

Useful primarily for debugging purposes.
"""

from flockwave.connections import Connection, ConnectionBase
from flockwave.server.model import ConnectionPurpose
from trio import current_time, open_nursery, sleep, sleep_until

__all__ = ()


class VirtualConnection(ConnectionBase):
    """Virtual connection class used by this extension.

    This connection class breaks the connection two seconds after it was
    opened. Subsequent attempts to open the connection will be blocked
    up to at least three seconds after the connection was closed the last
    time.
    """

    def __init__(self):
        """Constructor."""
        super().__init__()
        self._open_disallowed_until = None

    async def _open(self):
        """Opens the connection if it is currently allowed. Opening the
        connection will start a timer that closes the connection in
        two seconds.
        """
        if self._open_disallowed_until is not None:
            await sleep_until(self._open_disallowed_until)

    async def _close(self):
        """Closes the connection and blocks reopening attempts in the next
        three seconds.
        """
        self._open_disallowed_until = current_time() + 3

    async def close_soon(self):
        """Waits two seconds and then closes the connection."""
        await sleep(2)
        await self.close()


async def worker(app, configuration, logger):
    """Runs the main worker task of the extension when at least one client
    is connected.

    The configuration object supports the following keys:

    ``count``
        The number of virtual connections to provide

    ``id_format``
        String template that defines how the names of the virtual
        connections should be generated; must be in the format accepted
        by the ``str.format()`` method in Python when given the
        connection index as its argument.
    """
    count = configuration.get("count", 0)
    id_format = configuration.get("id_format", "virtualConnection{0}")

    async with open_nursery() as nursery:
        for index in range(count):
            name = id_format.format(index)
            nursery.start_soon(
                _handle_single_connection, app, VirtualConnection(), name
            )


async def _handle_single_connection(app, connection: Connection, name: str) -> None:
    with app.connection_registry.use(
        connection, name=name, purpose=ConnectionPurpose.debug
    ):
        await app.supervise(connection, task=VirtualConnection.close_soon)
