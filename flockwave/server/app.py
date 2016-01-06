"""Application object for the Flockwave server."""

from flask import Flask, request
from flask.ext.socketio import SocketIO


app = Flask(__name__)
app.secret_key = b'\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10'\
    b'\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef'
socketio = SocketIO(app, logger=True)


@socketio.on("connect")
def handle_connection():
    """Handler called when a client connects to the Flockwave server socket."""
    app.logger.info("Client {0} connected".format(request.sid))


@socketio.on("disconnect")
def handle_disconnection():
    """Handler called when a client disconnects from the server socket."""
    app.logger.info("Client {0} disconnected".format(request.sid))


@socketio.on("fw")
def handle_flockwave_message(message):
    """Handler called for all incoming Flockwave JSON messages."""
    app.logger.info("Got message! {0!r}".format(message))
