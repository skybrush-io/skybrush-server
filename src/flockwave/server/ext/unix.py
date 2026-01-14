"""Extension that provides Unix domain socket-based communication channels for
the server.

This extension enables the server to communicate with clients by expecting
requests on a certain Unix domain socket.
"""

from __future__ import annotations

import weakref

from contextlib import ExitStack
from functools import partial
from logging import Logger
from pathlib import Path
from tempfile import gettempdir
from trio import aclose_forcefully, CapacityLimiter, Lock, open_nursery, SocketStream
from typing import Any, Generic, Optional, Protocol, TypeVar, TYPE_CHECKING, cast

from flockwave.channels import ParserChannel
from flockwave.connections import serve_unix
from flockwave.encoders.json import create_json_encoder
from flockwave.parsers.json import create_json_parser
from flockwave.server.model import Client, CommunicationChannel
from flockwave.server.utils import overridden

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: SkybrushServer | None = None
encoder = create_json_encoder()
log: Logger | None = None
path: str | None = None

T = TypeVar("T")


class ClientWithStream(Protocol):
    stream: SocketStream | None = None


class UnixDomainSocketChannel(Generic[T], CommunicationChannel[T]):
    """Object that represents a Unix domain socket communication channel between
    a server and a single client.
    """

    client_ref: weakref.ref[ClientWithStream] | None = None
    lock: Lock
    stream: SocketStream | None = None

    def __init__(self):
        """Constructor."""
        self.client_ref = None
        self.stream = None
        self.lock = Lock()

    def bind_to(self, client: Client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("unix:"):
            self.client_ref = weakref.ref(cast("Any", client), self._erase_stream)
        else:
            raise ValueError("client has no ID or address yet")

    async def close(self, force: bool = False):
        stream = self._resolve_stream()
        if stream is None:
            return

        if force:
            await aclose_forcefully(stream)
        else:
            await stream.aclose()

    async def send(self, message: T) -> None:
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

    def _erase_stream(self, ref):
        self.stream = None

    def _resolve_stream(self) -> SocketStream | None:
        if self.stream is None and self.client_ref is not None:
            client = self.client_ref()
            if client is not None:
                self.stream = client.stream
            self.client_ref = None
        return self.stream


############################################################################


def get_ssdp_location(address: Any) -> Optional[str]:
    """Returns the SSDP location descriptor of the Unix domain socket channel.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    global path
    return f"unix:{path}" if path else None


async def handle_connection(stream: SocketStream, *, limit: CapacityLimiter):
    """Handles a connection attempt from a single client.

    Parameters:
        stream: a Trio socket stream that we can use to communicate with the
            client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    socket = stream.socket
    address = socket.getsockname()

    client_id = f"unix:{address}"
    handler = partial(handle_message, limit=limit)

    assert app is not None

    with app.client_registry.use(client_id, "unix") as client:
        client = cast(ClientWithStream, client)
        client.stream = stream
        async with open_nursery() as nursery:
            channel = ParserChannel(
                reader=stream.receive_some, parser=create_json_parser()
            )
            async for message in channel:
                nursery.start_soon(handler, message, client)


async def handle_connection_safely(stream: SocketStream, *, limit: CapacityLimiter):
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
        if log:
            log.exception(ex)


async def handle_message(message, client, *, limit: CapacityLimiter) -> None:
    """Handles a single message received from the given sender.

    Parameters:
        message: the incoming message, waiting to be parsed
        client: the client that sent the message
    """
    assert app is not None
    async with limit:
        await app.message_hub.handle_incoming_message(message, client)


############################################################################


async def run(app: SkybrushServer, configuration, logger: Logger):
    """Background task that is active while the extension is loaded."""
    path = configuration.get("path", str(Path(gettempdir()) / "skybrushd.sock"))
    pool_size = configuration.get("pool_size", 1000)

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger, path=path))
        stack.enter_context(
            app.channel_type_registry.use(
                "unix",
                factory=UnixDomainSocketChannel,
                ssdp_location=get_ssdp_location,
            )
        )

        limit = CapacityLimiter(pool_size)
        handler = partial(handle_connection_safely, limit=limit)

        await serve_unix(handler, path)
