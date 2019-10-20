"""Extension that provides Unix domain socket-based communication channels for
the server.

This extension enables the server to communicate with clients by expecting
requests on a certain Unix domain socket.
"""

import weakref

from contextlib import ExitStack
from functools import partial
from pathlib import Path
from tempfile import gettempdir
from trio import CapacityLimiter, Lock, open_nursery
from typing import Optional

from flockwave.channels import ParserChannel
from flockwave.connections import serve_unix
from flockwave.parsers import DelimiterBasedParser
from flockwave.encoders import JSONEncoder
from flockwave.server.model import CommunicationChannel
from flockwave.server.utils import overridden


app = None
encoder = JSONEncoder()
log = None
path = None


class UnixDomainSocketChannel(CommunicationChannel):
    """Object that represents a Unix domain socket communication channel between
    a server and a single client.
    """

    def __init__(self):
        """Constructor."""
        self.client_ref = None
        self.stream = None
        self.lock = Lock()

    def bind_to(self, client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("unix:"):
            self.client_ref = weakref.ref(client, self._erase_stream)
        else:
            raise ValueError("client has no ID or address yet")

    async def send(self, message):
        """Inherited."""
        if self.stream is None:
            self.stream = self.client_ref().stream
            self.client_ref = None

        async with self.lock:
            # Locking is needed, otherwise we could be running into problems
            # if a message was sent only partially but the message hub is
            # already trying to send another one (since the message hub
            # dispatches each message in a separate task)
            await self.stream.send_all(encoder.dumps(message) + b"\n")

    def _erase_stream(self, ref):
        self.stream = None


############################################################################


def get_address(in_subnet_of: Optional[str] = None) -> str:
    """Returns the address where we are listening for incoming connections.

    Parameters:
        in_subnet_of: ignored; does not apply to Unix domain sockets.

    Returns:
        the address where we are listening
    """
    global path
    return path


def get_ssdp_location(address) -> Optional[str]:
    """Returns the SSDP location descriptor of the Unix domain socket channel.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    global path
    return f"unix:{path}" if path else None


async def handle_connection(stream, *, limit):
    """Handles a connection attempt from a single client.

    Parameters:
        stream (SocketStream): a Trio socket stream that we can use to
            communicate with the client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    socket = stream.socket
    address = socket.getsockname()

    client_id = f"unix:{address}"
    handler = partial(handle_message, limit=limit)

    with app.client_registry.use(client_id, "unix") as client:
        client.stream = stream
        async with open_nursery() as nursery:
            channel = ParserChannel(
                reader=stream.receive_some, parser=DelimiterBasedParser()
            )
            async for line in channel:
                nursery.start_soon(handler, line, client)


async def handle_connection_safely(stream, *, limit):
    """Handles a connection attempt from a single client, ensuring
    that exceptions do not propagate through.

    Parameters:
        stream (SocketStream): a Trio socket stream that we can use to
            communicate with the client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    try:
        return await handle_connection(stream, limit=limit)
    except Exception as ex:
        # Exceptions raised during a connection are caught and logged here;
        # we do not let the main task itself crash because of them
        log.exception(ex)


async def handle_message(message: bytes, client, *, limit: CapacityLimiter) -> None:
    """Handles a single message received from the given sender.

    Parameters:
        message: the incoming message, waiting to be parsed
        client: the client that sent the message
    """
    try:
        message = encoder.loads(message)
    except ValueError as ex:
        log.warn(f"Malformed JSON message received from {client.id}: {message[:20]}")
        log.exception(ex)
        return

    async with limit:
        await app.message_hub.handle_incoming_message(message, client)


############################################################################


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    path = configuration.get("path", str(Path(gettempdir()) / "flockwaved.sock"))
    pool_size = configuration.get("pool_size", 1000)

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger, path=path))
        stack.enter_context(
            app.channel_type_registry.use(
                "unix",
                factory=UnixDomainSocketChannel,
                address=get_address,
                ssdp_location=get_ssdp_location,
            )
        )

        limit = CapacityLimiter(pool_size)
        handler = partial(handle_connection_safely, limit=limit)

        await serve_unix(handler, path)
