"""Application object for the Flockwave server."""

from __future__ import absolute_import

from flask import Flask, request
from flask.ext.socketio import SocketIO

from .ext.manager import ExtensionManager
from .logger import log
from .message_hub import MessageHub
from .model import FlockwaveMessage
from .uav_registry import UAVRegistry
from .version import __version__ as server_version

__all__ = ()

PACKAGE_NAME = __name__.rpartition(".")[0]


class FlockwaveServer(Flask):
    """Flask application object for the Flockwave server."""

    def __init__(self, *args, **kwds):
        super(FlockwaveServer, self).__init__(
            PACKAGE_NAME, *args, **kwds
        )
        self.prepare()

    def prepare(self):
        """Hook function that contains preparation steps that should be
        performed by the server before it starts serving requests.
        """
        # Load the configuration
        self.config.from_object(".".join([PACKAGE_NAME, "config"]))
        self.config.from_envvar("FLOCKWAVE_SETTINGS", silent=True)

        # Create a message hub that will handle incoming and outgoing
        # messages
        self.message_hub = MessageHub()

        # Create an object to hold information about all the UAVs that
        # the server knows about
        self.uav_registry = UAVRegistry()

        # Import and configure the extensions that we want to use.
        self.extension_manager = ExtensionManager(self)
        self.extension_manager.configure(self.config.get("EXTENSIONS", {}))


app = FlockwaveServer()
socketio = SocketIO(app)


@socketio.on("connect")
def handle_connection():
    """Handler called when a client connects to the Flockwave server socket."""
    log.info("Client connected", extra={"id": request.sid})


@socketio.on("disconnect")
def handle_disconnection():
    """Handler called when a client disconnects from the server socket."""
    log.info("Client disconnected", extra={"id": request.sid})


@socketio.on("fw")
def handle_flockwave_message(message):
    """Handler called for all incoming Flockwave JSON messages."""
    try:
        message = FlockwaveMessage(message)
    except:
        log.exception("Flockwave message does not match schema")
        return

    if "error" in message:
        log.warning("Error message from Flockwave client silently dropped")
        return

    if not app.message_hub.handle_incoming_message(message):
        log.warning(
            "Unhandled message: {0.body[type]}".format(message),
            extra={
                "id": message.id
            }
        )

# ######################################################################## #


@app.message_hub.on("SYS-VER")
def handle_SYS_VER(message, hub):
    return {
        "software": "flockwave-server",
        "version": server_version
    }


@app.message_hub.on("UAV-INF")
def handle_UAV_INF(message, hub):
    statuses = {}
    body = {"status": statuses}
    response = app.message_hub.create_response_to(message, body=body)

    for uav_id in message.body["ids"]:
        try:
            uav = app.uav_registry.find_uav_by_id(uav_id)
        except KeyError:
            response.add_failure(uav_id, "No such UAV")
            continue

        # TODO
        statuses[uav_id] = {}

    return response


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message, hub):
    return {
        "ids": list(app.uav_registry.ids)
    }


# ######################################################################## #
