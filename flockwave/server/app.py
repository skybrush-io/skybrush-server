"""Application object for the Flockwave server."""

from __future__ import absolute_import

from flask import Flask, request
from flask.ext.socketio import SocketIO, emit

from .logger import log
from .model import FlockwaveMessage, FlockwaveMessageBuilder
from .version import __version__ as server_version

__all__ = ()


app = Flask(__name__.rpartition(".")[0])
app.secret_key = b'\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10'\
    b'\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef'
socketio = SocketIO(app)

message_builder = FlockwaveMessageBuilder()


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

    log.info(
        "Received {0.body[type]} message".format(message),
        extra={
            "id": message.id,
            "semantics": "request"
        }
    )

    if message.body["type"] == "SYS-VER":
        response = {
            "software": "flockwave-server",
            "version": server_version
        }
        send_response(message, response)
    else:
        log.warning(
            "Unhandled message: {0.body[type]}".format(message),
            extra={
                "id": message.id
            }
        )


def send_response(message, body):
    """Sends a response to a message.

    Arguments:
        message (FlockwaveMessage): the message to respond to
        body (object): the body of the response to the message

    Returns:
        the newly constructed response that was sent back to the client
    """
    if "type" not in body:
        body["type"] = message.body["type"]

    response = message_builder.create_response_to(message)
    response.body = body

    log.info(
        "Sending {0.body[type]} response".format(response),
        extra={
            "id": message.id,
            "semantics": "response_success"
        }
    )

    emit("fw", response, json=True)
    return response
