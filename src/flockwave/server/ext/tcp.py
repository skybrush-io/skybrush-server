"""Extension that provides TCP socket-based communication channels for the
server.

This extension enables the server to communicate with clients by expecting
requests on a certain TCP port.
"""

import weakref

from contextlib import ExitStack
from functools import partial
from json import JSONDecodeError
from logging import Logger
from trio import (
    aclose_forcefully,
    BrokenResourceError,
    CapacityLimiter,
    ClosedResourceError,
    Lock,
    open_nursery,
    SocketStream,
)
from typing import Any, Optional

from flockwave.channels import ParserChannel
from flockwave.encoders.json import create_json_encoder
from flockwave.parsers.json import create_json_parser
from flockwave.server.model import Client, CommunicationChannel
from flockwave.server.ports import get_port_number_for_service
from flockwave.networking import format_socket_address, get_socket_address
from flockwave.server.utils import overridden
from flockwave.server.utils.networking import serve_tcp_and_log_errors

app = None
encoder = create_json_encoder()
log: Optional[Logger] = None


class TCPChannel(CommunicationChannel):
    """Object that represents a TCP communication channel between a
    server and a single client.
    """

    client_ref: Optional["weakref.ref[Client]"]
    lock: Lock

    def __init__(self):
        """Constructor."""
        self.address = None
        self.client_ref = None
        self.lock = Lock()
        self.stream = None

    def bind_to(self, client: Client) -> None:
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("tcp://"):
            host, _, port = client.id[6:].rpartition(":")
            self.address = host, int(port)
            self.client_ref = weakref.ref(client, self._erase_stream)
        else:
            raise ValueError("client has no ID or address yet")

    async def close(self, force: bool = False) -> None:
        if self.stream is None:
            if self.client_ref is not None:
                self.stream = self.client_ref().stream
                self.client_ref = None
            else:
                print("No client and no client_ref yet")
                return

        if force:
            await aclose_forcefully(self.stream)
        else:
            await self.stream.aclose()

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
            await self.stream.send_all(encoder(message))

    def _erase_stream(self, ref) -> None:
        self.stream = None


############################################################################


def get_address(in_subnet_of: Optional[str] = None) -> str:
    """Returns the address where we are listening for incoming TCP connections.

    Parameters:
        in_subnet_of: when not `None` and we are listening on multiple (or
            all) interfaces, this address is used to pick a reported address
            that is in the same subnet as the given address

    Returns:
        the address where we are listening
    """
    global sock
    return get_socket_address(sock)


def get_ssdp_location(address, host, port) -> Optional[str]:
    """Returns the SSDP location descriptor of the TCP channel.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    return format_socket_address(
        (host, port), format="tcp://{host}:{port}", in_subnet_of=address
    )


async def handle_connection(stream, *, limit):
    """Handles a connection attempt from a single client.

    Parameters:
        stream (SocketStream): a Trio socket stream that we can use to
            communicate with the client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    socket = stream.socket
    address = socket.getpeername()

    client_id = "tcp://{0}:{1}".format(*address)
    handler = partial(handle_message, limit=limit)
    parser = create_json_parser()

    with app.client_registry.use(client_id, "tcp") as client:
        client.stream = stream
        async with open_nursery() as nursery:
            channel = ParserChannel(reader=stream.receive_some, parser=parser)
            try:
                async for line in channel:
                    nursery.start_soon(handler, line, client)
            except BrokenResourceError:
                # This is okay, the other side closed the connection
                pass
            except ClosedResourceError:
                # This is okay, we closed the connection
                pass
            except JSONDecodeError as ex:
                # Parse error, probably trying to connect via WebSocket.
                if log:
                    log.error(f"Parse error: {ex}")


async def handle_connection_safely(stream: SocketStream, *, limit: CapacityLimiter):
    """Handles a connection attempt from a single client, ensuring
    that exceptions do not propagate through.

    Parameters:
        stream: a Trio socket stream that we can use to communicate with the client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    try:
        return await handle_connection(stream, limit=limit)
    except Exception as ex:
        # Exceptions raised during a connection are caught and logged here;
        # we do not let the main task itself crash because of them
        if log:
            log.exception(ex)


async def handle_message(message: Any, client, *, limit: CapacityLimiter) -> None:
    """Handles a single message received from the given sender.

    Parameters:
        message: the incoming message
        client: the client that sent the message
    """
    async with limit:
        await app.message_hub.handle_incoming_message(message, client)


############################################################################


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    host = configuration.get("host", "")
    port = configuration.get("port", get_port_number_for_service("tcp"))
    pool_size = configuration.get("pool_size", 1000)

    if not host:
        host = None  # empty string is not okay on Linux

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))
        stack.enter_context(
            app.channel_type_registry.use(
                "tcp",
                factory=TCPChannel,
                address=get_address,
                ssdp_location=partial(get_ssdp_location, host=host, port=port),
            )
        )

        limit = CapacityLimiter(pool_size)
        handler = partial(handle_connection_safely, limit=limit)

        await serve_tcp_and_log_errors(handler, port, host=host, log=logger)


description = "TCP socket-based communication channel"
schema = {
    "properties": {
        "host": {
            "type": "string",
            "title": "Host",
            "description": (
                "IP address of the host that the server should listen on for "
                "incoming TCP connections. Use an empty string to listen on all "
                "interfaces, or 127.0.0.1 to listen on localhost only"
            ),
            "default": "",
            "propertyOrder": 10,
        },
        "port": {
            "type": "integer",
            "title": "Port",
            "description": (
                "Port that the server should listen on for incoming TCP connections. "
                "Untick the checkbox to let the server derive the port number from "
                "its own base port."
            ),
            "minimum": 1,
            "maximum": 65535,
            "default": get_port_number_for_service("tcp"),
            "required": False,
            "propertyOrder": 20,
        },
        "pool_size": {
            "type": "integer",
            "title": "Connection pool size",
            "minimum": 1,
            "description": ("Maximum number of concurrent TCP connections to handle."),
            "default": 1000,
            "propertyOrder": 30,
        },
    }
}
