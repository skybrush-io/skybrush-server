"""Extension that provides TCP socket-based communication channels for the
server.

This extension enables the server to communicate with clients by expecting
requests on a certain TCP port.
"""

from __future__ import annotations

import weakref
from contextlib import ExitStack
from functools import partial
from json import JSONDecodeError
from logging import Logger
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast

from trio import (
    BrokenResourceError,
    CapacityLimiter,
    ClosedResourceError,
    Lock,
    SocketStream,
    aclose_forcefully,
    open_nursery,
)

from flockwave.channels import ParserChannel
from flockwave.connections import IPAddressAndPort
from flockwave.encoders.json import create_json_encoder
from flockwave.networking import format_socket_address
from flockwave.parsers.json import create_json_parser
from flockwave.server.model import Client, CommunicationChannel
from flockwave.server.ports import suggest_port_number_for_service, use_port
from flockwave.server.utils import overridden
from flockwave.server.utils.networking import serve_tcp_and_log_errors

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: SkybrushServer | None = None
address: IPAddressAndPort | None = None
encoder = create_json_encoder()
log: Logger | None = None

T = TypeVar("T")


class ClientWithStream(Protocol):
    stream: SocketStream | None = None


class TCPChannel(Generic[T], CommunicationChannel[T]):
    """Object that represents a TCP communication channel between a
    server and a single client.
    """

    address: tuple[str, int] | None = None
    client_ref: weakref.ref[ClientWithStream] | None = None
    lock: Lock
    stream: SocketStream | None = None

    def __init__(self):
        """Constructor."""
        self.lock = Lock()

    def bind_to(self, client: Client) -> None:
        """Binds the communication channel to the given client.

        Parameters:
            client: the client to bind the channel to
        """
        if client.id and client.id.startswith("tcp://"):
            host, _, port = client.id[6:].rpartition(":")
            self.address = host, int(port)
            self.client_ref = weakref.ref(cast("Any", client), self._erase_stream)
        else:
            raise ValueError("client has no ID or address yet")

    async def close(self, force: bool = False) -> None:
        stream = self._resolve_stream()
        if stream is None:
            return

        if force:
            await aclose_forcefully(stream)
        else:
            await stream.aclose()

    async def send(self, message: T):
        """Inherited."""
        stream = self._resolve_stream()
        if stream is None:
            raise RuntimeError("cannot send message, channel is not bound to a stream")

        async with self.lock:
            # Locking is needed, otherwise we could be running into problems
            # if a message was sent only partially but the message hub is
            # already trying to send another one (since the message hub
            # dispatches each message in a separate task)
            await stream.send_all(encoder(message))

    def _erase_stream(self, ref) -> None:
        self.stream = None

    def _resolve_stream(self) -> SocketStream | None:
        if self.stream is None and self.client_ref is not None:
            client = self.client_ref()
            if client is not None:
                self.stream = client.stream
            self.client_ref = None
        return self.stream


############################################################################


def get_ssdp_location(
    address: IPAddressAndPort | None, host: str, port: int
) -> str | None:
    """Returns the SSDP location descriptor of the TCP channel.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    return format_socket_address(
        (host, port), format="tcp://{host}:{port}", in_subnet_of=address
    )


async def handle_connection(stream: SocketStream, *, limit: CapacityLimiter):
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

    assert app is not None

    with app.client_registry.use(client_id, "tcp") as client:
        client = cast(ClientWithStream, client)
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
        await app.message_hub.handle_incoming_message(message, client)  # type: ignore


############################################################################


async def run(app: SkybrushServer, configuration, logger: Logger):
    """Background task that is active while the extension is loaded."""
    host = str(configuration.get("host", ""))
    port = int(configuration.get("port", suggest_port_number_for_service("tcp")))
    pool_size = int(configuration.get("pool_size", 1000))
    address = host, port

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger, address=address))
        stack.enter_context(use_port("tcp", port))
        stack.enter_context(
            app.channel_type_registry.use(
                "tcp",
                factory=TCPChannel,
                ssdp_location=partial(get_ssdp_location, host=host, port=port),
            )
        )

        limit = CapacityLimiter(pool_size)
        handler = partial(handle_connection_safely, limit=limit)

        # empty string is not okay on Linux for the hostname so use None
        await serve_tcp_and_log_errors(handler, port, host=host or None, log=logger)


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
            "default": suggest_port_number_for_service("tcp"),
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
