"""Application object for the Flockwave server."""

from __future__ import absolute_import

import json

from datetime import datetime
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


class _JSONEncoder(object):
    """Custom JSON encoder and decoder function to be used by Socket.IO."""

    def __init__(self):
        self.encoder = json.JSONEncoder(
            separators=(",", ":"), sort_keys=False, indent=None,
            default=self._encode
        )
        self.decoder = json.JSONDecoder()

    def _encode(self, obj):
        """Encodes an object that could otherwise not be encoded into JSON.

        This function performs the following conversions:

        - ``datetime.datetime`` objects are converted into a standard
          ISO-8601 string representation

        - Objects having a ``json`` property will be replaced by the value
          of this property

        Parameters:
            obj (object): the object to encode

        Returns:
            object: the JSON representation of the object
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, "json"):
            return obj.json
        else:
            raise TypeError("cannot encode {0!r} into JSON".format(obj))

    def dumps(self, obj, *args, **kwds):
        """Converts the given object into a JSON string representation.
        Additional positional or keyword arguments that may be passed by
        Socket.IO are silently ignored.

        Parameters:
            obj (object): the object to encode into a JSON string

        Returns:
            str: a string representation of the given object in JSON
        """
        return self.encoder.encode(obj)

    def loads(self, data, *args, **kwds):
        """Loads a JSON-encoded object from the given string representation.
        Additional positional or keyword arguments that may be passed by
        Socket.IO are silently ignored.

        Parameters:
            data (str): the string to decode

        Returns:
            object: the constructed object
        """
        return self.decoder.decode(data)


app = FlockwaveServer()
socketio = SocketIO(app, json=_JSONEncoder())


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
        message = FlockwaveMessage.from_json(message)
    except Exception:
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


@socketio.on_error_default
def handle_exception(exc):
    """Handler that is called when an exception happens during Socket.IO
    message handling.
    """
    log.exception("Exception while handling message")


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
        statuses[uav_id] = uav.json

    return response


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message, hub):
    return {
        "ids": list(app.uav_registry.ids)
    }


# ######################################################################## #
