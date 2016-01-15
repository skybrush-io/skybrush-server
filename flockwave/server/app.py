"""Application object for the Flockwave server."""

from __future__ import absolute_import

from flask import Flask, request
from flask.ext.socketio import SocketIO

from .logger import log
from .message_hub import MessageHub
from .model import FlockwaveMessage
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

        Logging is not set up by the time when this function is called;
        invocations of logging methods will not produce any output on the
        console.
        """
        # Import and configure the extensions that we want to use.
        # This is hardcoded now but should be done in a configuration file
        # later on.
        pass


app = FlockwaveServer()
app.config.from_object(".".join([PACKAGE_NAME, "config"]))
app.config.from_envvar("FLOCKWAVE_SETTINGS", silent=True)

socketio = SocketIO(app)

message_hub = MessageHub()


@app.route("/")
def index():
    return app.send_static_file("index.html")


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

    if not message_hub.handle_incoming_message(message):
        log.warning(
            "Unhandled message: {0.body[type]}".format(message),
            extra={
                "id": message.id
            }
        )

# ######################################################################## #


@message_hub.on("SYS-VER")
def handle_SYS_VER(message, hub):
    response = {
        "software": "flockwave-server",
        "version": server_version
    }
    hub.send_response(message, response)
    return True


# ######################################################################## #
