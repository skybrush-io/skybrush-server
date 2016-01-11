"""Application object for the Flockwave server."""

from __future__ import absolute_import

from flask import Flask, request
from flask.ext.socketio import SocketIO

from .logger import log
from .model import FlockwaveMessage


__all__ = ()


app = Flask(__name__)
app.secret_key = b'\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10'\
    b'\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef'
socketio = SocketIO(app)


@socketio.on("connect")
def handle_connection():
    """Handler called when a client connects to the Flockwave server socket."""
    log.info("Client {0} connected".format(request.sid))


@socketio.on("disconnect")
def handle_disconnection():
    """Handler called when a client disconnects from the server socket."""
    log.info("Client {0} disconnected".format(request.sid))


@socketio.on("fw")
def handle_flockwave_message(message):
    """Handler called for all incoming Flockwave JSON messages."""
    try:
        message = FlockwaveMessage(message)
    except:
        log.exception("Flockwave message does not match schema")
        return

    log.info(
        "Got message! {0!r}".format(message),
        extra={"semantics": "request"}
    )
