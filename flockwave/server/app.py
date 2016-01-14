"""Application object for the Flockwave server."""

from __future__ import absolute_import

from flask import Flask, request
from flask.ext.socketio import SocketIO

from .logger import log
from .message_hub import MessageHub
from .model import FlockwaveMessage
from .version import __version__ as server_version

__all__ = ()


app = Flask(__name__.rpartition(".")[0])
app.secret_key = b'\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10'\
    b'\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef'
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
