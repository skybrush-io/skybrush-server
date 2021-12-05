"""Extension that provides Socket.IO communication channels for the server.

This extension enables the server to communicate with clients using
Socket.IO connections.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from enum import Enum
from functools import partial
from json import JSONDecoder
from logging import Logger
from trio import open_nursery, sleep_forever
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Tuple,
    TYPE_CHECKING,
)
from urllib.parse import parse_qs

from flockwave.encoders.json import create_json_encoder
from flockwave.server.model import Client, CommunicationChannel
from flockwave.networking import format_socket_address

from .vendor.socketio_v4 import TrioServer as TrioServerForSocketIOV4
from .vendor.socketio_v5 import TrioServer as TrioServerForSocketIOV5

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


class SocketIOChannel(CommunicationChannel):
    """Object that represents a Socket.IO communication channel between a
    server and a single client.
    """

    _client_id_prefix: str
    _socketio_session_id: str

    def __init__(self, socketio, client_id_prefix: str):
        """Constructor.

        Parameters:
            socketio: the Socket.IO server object that handles the client
                connected to this communication channel
            client_id_prefix: prefix to prepend to the Socket.IO session ID
                in order to derive the client ID on our side
        """
        super().__init__()
        self.socketio = socketio
        self._client_id_prefix = client_id_prefix + ":"

    def bind_to(self, client: Client) -> None:
        """Binds the communication channel to the given Socket.IO client
        session ID.
        """
        if client.id and client.id.startswith(self._client_id_prefix):
            self._socketio_session_id = client.id[len(self._client_id_prefix) :]
        else:
            raise ValueError("client has no ID yet")

    async def close(self, force: bool = False) -> None:
        # There is no forceful disconnection in the Socket.IO module so we
        # simply try once again if force is True
        await self.socketio.disconnect(self._socketio_session_id)

    async def send(self, message) -> None:
        """Inherited."""
        await self.socketio.emit(
            "fw", message, room=self._socketio_session_id, namespace="/"
        )


############################################################################


class JSONEncoder:
    def __init__(self):
        self.encoder = create_json_encoder()
        self.parser = JSONDecoder()

    def dumps(self, obj, *args, **kwds):
        # There is an unnecessary back-and-forth UTF-8 encoding here because
        # create_json_encoder() and create_json_parser() return raw bytes,
        # but TrioServer needs strings
        return self.encoder(obj).decode("utf-8")

    def loads(self, data, *args, **kwds):
        return self.parser.decode(data)


############################################################################


class SocketIOProtocol(Enum):
    """Enum containing string identifiers for the supported Socket.IO
    protocols.
    """

    channel_id: str
    server_class: Callable
    expected_engine_io_query_param: List[str]

    def __new__(
        cls, value: str, channel_id: str, server_class: Callable, engine_io_version: int
    ):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.channel_id = channel_id
        obj.server_class = server_class
        obj.expected_engine_io_query_param = [str(engine_io_version)]
        return obj

    @classmethod
    def from_string(cls, value: str):
        for item in cls:
            if item.value == value:
                return item
        raise ValueError(f"No such SocketIOProtocol: {value!r}")

    def accepts_wsgi_environment(self, environ: Dict[str, Any]) -> bool:
        """Returns whether this protocol is able to serve requests with the given
        WSGI environment.

        For Socket.IO and Engine.IO, we simply need to check the `EIO` query
        parameter of the request. Socket.IO v4 is based on Engine.IO v3 so
        `EIO=3`. Socket.IO v5 is based on Engine.IO v5 so `EIO=4`.
        """
        query_string = environ.get("QUERY_STRING")
        if query_string:
            query = parse_qs(query_string)
            return query.get("EIO") == self.expected_engine_io_query_param
        else:
            return False

    SOCKETIO_V4 = ("socketio-v4", "sio", TrioServerForSocketIOV4, 3)
    SOCKETIO_V5 = ("socketio-v5", "sio5", TrioServerForSocketIOV5, 4)


def get_enabled_protocols(
    configuration: Dict[str, Any], logger: Logger
) -> List[SocketIOProtocol]:
    """Retrieves the list of enabled Socket.IO protocols from the configuration.

    Returns:
        a list containing the enabled Socket.IO protocols from the configuration.
    """
    protocols: Optional[Iterable[str]] = configuration.get("protocols")
    if protocols is not None and not isinstance(protocols, (list, tuple)):
        logger.warn("'protocols' configuration key must be a list, ignoring")
        protocols = None

    result: List[SocketIOProtocol] = []
    for protocol_code in protocols or ("socketio-v4", "socketio-v5"):
        try:
            protocol = SocketIOProtocol.from_string(protocol_code)
            result.append(protocol)
        except Exception:
            logger.warn(f"Ignoring unknown protocol from configuration: {protocol!r}")

    return result


class SocketIOCommunicationHandler:

    _app: "SkybrushServer"
    _prefix: str
    _protocol: SocketIOProtocol

    def __init__(self, app: "SkybrushServer", protocol: SocketIOProtocol):
        self._app = app
        self._protocol = protocol
        self._prefix = self._protocol.channel_id

    def _convert_client_id(self, client_id: str) -> str:
        """Converts a client ID used in the Socket.IO context to a client ID
        that we register in the client registry.

        The Socket.IO client ID is prefixed with the client ID prefix of the
        protocol to make it easier to distinguish Socket.IO clients following
        the various versions of Socket.IO protocols in our own registry.
        """
        return f"{self._prefix}:{client_id}"

    def _get_ssdp_location(
        self, channel_id: str, address: Optional[Tuple[str, int]]
    ) -> Optional[str]:
        """Returns the SSDP location descriptor of the Socket.IO channel
        corresponding to the Socket.IO protocol of this instance.

        Parameters:
            channel_id: identifier that uniquely identifies a Socket.IO channel type
                (e.g., `sio` or `sio5`). See the SocketIOProtocol_ enum properties.
            address: when not `None` and we are listening on multiple (or all)
                interfaces, this address is used to pick a reported address that
                is in the same subnet as the given address
        """
        app_address = self._app.import_api("http_server").address if self._app else None
        format_str = channel_id + "://{host}:{port}"
        return (
            format_socket_address(app_address, format=format_str, in_subnet_of=address)
            if app_address
            else None
        )

    def _handle_connection(self, client_id: str, environ) -> None:
        """Handler called when a client connects to the Skybrush server
        socket.
        """
        client = self._app.client_registry.add(
            self._convert_client_id(client_id), self._prefix
        )
        client.user = environ.get("REMOTE_USER")

    def _handle_disconnection(self, client_id: str) -> None:
        """Handler called when a client disconnects from the server socket."""
        self._app.client_registry.remove(self._convert_client_id(client_id))

    async def _handle_incoming_message(self, client_id: str, message) -> None:
        """Handler called for all incoming Flockwave JSON messages.

        Parameters:
            message (dict): the decoded JSON message as an ordinary Python
                dict. This message has not gone through validation yet and
                its members have not been cast to the appropriate data types
                on the Python side.
        """
        client_id = self._convert_client_id(client_id)
        try:
            client = self._app.client_registry[client_id]
        except KeyError:
            # client disconnected in the meanwhile; let's ignore the message
            return
        await self._app.message_hub.handle_incoming_message(message, client)

    @contextmanager
    def use(self) -> Iterator:
        server = self._protocol.server_class(
            json=JSONEncoder(), async_mode="asgi", cors_allowed_origins="*"
        )

        server.on("connect")(self._handle_connection)
        server.on("disconnect")(self._handle_disconnection)
        server.on("fw")(self._handle_incoming_message)

        channel_id = self._protocol.channel_id

        broadcast_message = partial(server.emit, "fw", namespace="/")

        with self._app.channel_type_registry.use(
            channel_id,
            factory=partial(SocketIOChannel, server, channel_id),
            broadcaster=broadcast_message,
            ssdp_location=partial(self._get_ssdp_location, channel_id),
        ):
            yield server


############################################################################


async def run(app, configuration: Dict[str, Any], logger: Logger):
    # Check whether the user has enabled Socket.IO v4, Socket.IO v5 or both.
    protocols = get_enabled_protocols(configuration, logger)
    if not protocols:
        logger.warn("No protocols enabled in configuration")
        return

    socketio_servers = []

    with ExitStack() as stack:
        for protocol in protocols:
            manager = SocketIOCommunicationHandler(app, protocol)
            server = stack.enter_context(manager.use())
            socketio_servers.append(server)

        if len(socketio_servers) > 1:
            # We are going to run Socket.IO servers in parallel, and have one
            # "master" request handler that dispatches incoming connections to
            # the appropriate server based on the value of the EIO keyword
            # argument (EIO=3 goes to Socket.IO v4, EIO=4 goes to Socket.IO v5)
            from .vendor.engineio_v4.async_drivers.asgi import translate_request

            async def handle_requests_for_multiple_protocols(scope, receive, send):
                # Convert the incoming ASGI request to WSGI and find the protocol
                # that accepts it
                environ = await translate_request(scope, receive, send)
                for protocol, server in zip(protocols, socketio_servers):
                    if protocol.accepts_wsgi_environment(environ):
                        if protocol is SocketIOProtocol.SOCKETIO_V4:
                            # There is a discrepancy in translate_request()
                            # between Engine.IO v3 and v4; in v3, it has a
                            # side effect that accepts the websocket connection
                            # when the incoming event is a 'websocket.connect'
                            # event. Since we are calling _handle_wsgi_request()
                            # on Engine.IO v3 here, we need to accept the
                            # connection ourselves because we used translate_request()
                            # from Engine.IO v4
                            if environ["asgi.scope"]["type"] == "websocket":
                                await environ["asgi.send"]({"type": "websocket.accept"})
                        return await server._handle_wsgi_request(environ)

                # None of the protocols accepted the request; send it to the
                # first one anyway so we get _some_ kind of response that we
                # can pass on to the user
                return await socketio_servers[0]._handle_wsgi_request(environ)

            handle_request = handle_requests_for_multiple_protocols
        else:
            # Broadcast function can dispatch to a single server only
            handle_request = socketio_servers[0].handle_request

        stack.enter_context(
            app.import_api("http_server").mounted(
                handle_request, scopes=("http", "websocket"), path="/socket.io"
            )
        )

        async with open_nursery() as nursery:
            for server in socketio_servers:
                server.eio.use_nursery(nursery)  # type: ignore
            await sleep_forever()


dependencies = ("http_server",)
description = "Socket.IO communication channel"
schema = {}
