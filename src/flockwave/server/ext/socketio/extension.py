"""Extension that provides Socket.IO communication channels for the server.

This extension enables the server to communicate with clients using
Socket.IO connections.
"""

from contextlib import ExitStack
from functools import partial
from json import JSONDecoder
from trio import open_nursery, sleep_forever

from flockwave.encoders.json import create_json_encoder
from flockwave.server.model import CommunicationChannel
from flockwave.networking import format_socket_address
from flockwave.server.utils import overridden

from .vendor.socketio import TrioServer

app = None
log = None


class SocketIOChannel(CommunicationChannel):
    """Object that represents a Socket.IO communication channel between a
    server and a single client.
    """

    def __init__(self, socketio):
        """Constructor.

        Parameters:
            socketio (SocketIO): the Socket.IO handler object
        """
        super(SocketIOChannel, self).__init__()
        self.socketio = socketio

    def bind_to(self, client):
        """Binds the communication channel to the given Socket.IO client
        session ID.
        """
        if client.id and client.id.startswith("sio:"):
            self.sid = client.id[4:]
        else:
            raise ValueError("client has no ID yet")

    async def send(self, message):
        """Inherited."""
        await self.socketio.emit("fw", message, room=self.sid, namespace="/")


############################################################################


def get_ssdp_location(address):
    """Returns the SSDP location descriptor of the Socket.IO channel.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    app_address = app.import_api("http_server").address if app else None
    return (
        format_socket_address(
            app_address, format="sio://{host}:{port}", in_subnet_of=address
        )
        if app_address
        else None
    )


############################################################################


def _convert_client_id(client_id: str) -> str:
    """Converts a client ID used in the Socket.IO context to a client ID that
    we register in the client registry.

    The Socket.IO client ID is prefixed with `sio:` to make it easier to
    distinguish Socket.IO clients in our own registry.
    """
    return f"sio:{client_id}"


############################################################################


def handle_connection(client_id, environ):
    """Handler called when a client connects to the Flockwave server socket."""
    client = app.client_registry.add(_convert_client_id(client_id), "sio")
    client.user = environ.get("REMOTE_USER")


def handle_disconnection(client_id):
    """Handler called when a client disconnects from the server socket."""
    app.client_registry.remove(_convert_client_id(client_id))


async def handle_flockwave_message(client_id, message):
    """Handler called for all incoming Flockwave JSON messages.

    Parameters:
        message (dict): the decoded JSON message as an ordinary Python
            dict. This message has not gone through validation yet and
            its members have not been cast to the appropriate data types
            on the Python side.
    """
    client_id = _convert_client_id(client_id)
    client = app.client_registry[client_id]
    await app.message_hub.handle_incoming_message(message, client)


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


async def run(app, configuration, logger):
    socketio = TrioServer(
        json=JSONEncoder(), async_mode="asgi", cors_allowed_origins="*"
    )

    socketio.on("connect")(handle_connection)
    socketio.on("disconnect")(handle_disconnection)
    socketio.on("fw")(handle_flockwave_message)

    async def broadcast_message(message):
        """Broadcasts a message to all connected Socket.IO clients."""
        await socketio.emit("fw", message, namespace="/")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))
        stack.enter_context(
            app.channel_type_registry.use(
                "sio",
                factory=partial(SocketIOChannel, socketio),
                broadcaster=broadcast_message,
                ssdp_location=get_ssdp_location,
            )
        )
        stack.enter_context(
            app.import_api("http_server").mounted(
                socketio.handle_request, scopes=("http", "websocket"), path="/socket.io"
            )
        )

        async with open_nursery() as nursery:
            socketio.eio.use_nursery(nursery)
            await sleep_forever()


dependencies = ("http_server",)
