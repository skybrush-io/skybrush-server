"""Extension that provides Socket.IO communication channels for the server.

This extension enables the server to communicate with clients using
Socket.IO connections.
"""

from flask import request
from flask_jwt import current_identity as jwt_identity
from flask_socketio import SocketIO
from functools import partial

from ..authentication import jwt_optional
from ..encoders import JSONEncoder
from ..model import CommunicationChannel
from ..networking import format_socket_address


app = None
log = None
socketio = None


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

    def send(self, message):
        """Inherited."""
        self.socketio.emit(
            "fw", message, room=self.sid, namespace="/"
        )


############################################################################

def broadcast_message(message):
    """Broadcasts a message to all connected Socket.IO clients."""
    global socketio
    socketio.emit("fw", message, namespace="/")


def create_new_channel():
    """Creates a new SocketIOChannel_ instance that is not bound to
    any particular client yet.

    Returns:
        SocketIOChannel: the constructed channel
    """
    return SocketIOChannel(socketio)


def get_client():
    """Returns the Client_ object representing the client that sent the
    current Flask request being handled.
    """
    return app.client_registry[get_client_id()]


def get_client_id():
    """Returns the client ID from the current Flask request being handled.

    Returns:
        str: the ID of the client that sent the current Socket.IO message
    """
    return "sio:{0}".format(request.sid)

############################################################################


def get_ssdp_location(address):
    """Returns the SSDP location descriptor of the Socket.IO channel."""
    if app is None:
        return None
    else:
        app_address = getattr(app, "address", None)
        return format_socket_address(
            app_address, format="sio://{host}:{port}",
            remote_address=address
        )


@jwt_optional()
def handle_connection():
    """Handler called when a client connects to the Flockwave server socket."""
    # Update the condition below to enable mandatory JWT authentication
    if False and not jwt_identity:
        log.warning("Access denied because of lack of JWT identity")
        return False
    else:
        app.client_registry.add(get_client_id(), "sio")


def handle_disconnection():
    """Handler called when a client disconnects from the server socket."""
    app.client_registry.remove(get_client_id())


def handle_flockwave_message(message):
    """Handler called for all incoming Flockwave JSON messages.

    Parameters:
        message (dict): the decoded JSON message as an ordinary Python
            dict. This message has not gone through validation yet and
            its members have not been cast to the appropriate data types
            on the Python side.
    """
    app.message_hub.handle_incoming_message(message, get_client())


def handle_exception(exc):
    """Handler that is called when an exception happens during Socket.IO
    message handling.
    """
    log.exception("Exception while reading Socket.IO message")

############################################################################


def load(app, configuration, logger):
    """Loads the extension."""
    app.channel_type_registry.add(
        "sio",
        factory=create_new_channel, broadcaster=broadcast_message,
        ssdp_location=get_ssdp_location
    )
    socketio = SocketIO(app, json=JSONEncoder())

    socketio.on("connect")(handle_connection)
    socketio.on("disconnect")(handle_disconnection)
    socketio.on("fw")(handle_flockwave_message)
    socketio.on_error_default(handle_exception)

    app.runner = partial(socketio.run, app, use_reloader=False)

    globals().update(
        app=app,
        log=logger,
        socketio=socketio
    )


def unload(app, configuration):
    """Unloads the extension."""
    # Socket.IO currently does not allow event handlers to be deregistered
    # so this is not a complete solution yet; we cannot undo all the
    # socketio.on() calls from load()
    app.channel_type_registry.remove("sio")
    app.runner = None
    globals().update(app=None, log=None, socketio=None)
