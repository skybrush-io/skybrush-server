"""Application object for the Flockwave server."""

from __future__ import absolute_import

import json

from blinker import Signal
from datetime import datetime
from enum import Enum
from flask import Flask, request
from flask.ext.socketio import SocketIO

from .ext.manager import ExtensionManager
from .client_registry import ClientRegistry
from .connection_registry import ConnectionRegistry
from .logger import log
from .message_hub import MessageHub
from .model import FlockwaveMessage
from .uav_registry import UAVRegistry
from .version import __version__ as server_version

__all__ = ()

PACKAGE_NAME = __name__.rpartition(".")[0]


class FlockwaveServer(Flask):
    """Flask application object for the Flockwave server.

    Attributes:
        client_registry (ClientRegistry): central registry for the clients
            that are currently connected to the server
        client_count_changed (Signal): signal that is emitted when the
            number of clients connected to the server changes
        extension_manager (ExtensionManager): object that manages the
            loading and unloading of server extensions
        message_hub (MessageHub): central messaging hub via which one can
            send Flockwave messages
        uav_registry (UAVRegistry): central registry for the UAVs known to
            the server
    """

    num_clients_changed = Signal()

    def __init__(self, *args, **kwds):
        super(FlockwaveServer, self).__init__(
            PACKAGE_NAME, *args, **kwds
        )
        self.prepare()

    def create_CONN_INF_message_for(self, connection_ids,
                                    in_response_to=None):
        """Creates a CONN-INF message that contains information regarding
        the connections with the given IDs.

        Parameters:
            connection_ids (iterable): list of connection IDs
            in_response_to (FlockwaveMessage or None): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the CONN-INF message with the status info of
                the given connections
        """
        statuses = {}

        body = {"status": statuses, "type": "CONN-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

        for connection_id in connection_ids:
            try:
                entry = self.connection_registry.find_by_id(connection_id)
            except KeyError:
                response.add_failure(connection_id, "No such connection")
                continue
            statuses[connection_id] = entry.json

        return response

    def create_UAV_INF_message_for(self, uav_ids, in_response_to=None):
        """Creates an UAV-INF message that contains information regarding
        the UAVs with the given IDs.

        Parameters:
            uav_ids (iterable): list of UAV IDs
            in_response_to (FlockwaveMessage or None): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the UAV-INF message with the status info of
                the given UAVs
        """
        statuses = {}

        body = {"status": statuses, "type": "UAV-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

        for uav_id in uav_ids:
            try:
                uav = self.uav_registry.find_by_id(uav_id)
            except KeyError:
                response.add_failure(uav_id, "No such UAV")
                continue
            statuses[uav_id] = uav.json

        return response

    @property
    def num_clients(self):
        """The number of clients connected to the server."""
        return self.client_registry.num_entries

    def prepare(self):
        """Hook function that contains preparation steps that should be
        performed by the server before it starts serving requests.
        """
        # Load the configuration
        self.config.from_object(".".join([PACKAGE_NAME, "config"]))
        self.config.from_envvar("FLOCKWAVE_SETTINGS", silent=True)

        # Create an object to hold information about all the connected
        # clients that the server can talk to
        self.client_registry = ClientRegistry()
        self.client_registry.count_changed.connect(
            self._on_client_count_changed,
            sender=self.client_registry
        )

        # Creates an object to hold information about all the connections
        # to external data sources that the server manages
        self.connection_registry = ConnectionRegistry()
        self.connection_registry.connection_state_changed.connect(
            self._on_connection_state_changed,
            sender=self.connection_registry
        )

        # Create a message hub that will handle incoming and outgoing
        # messages
        self.message_hub = MessageHub()

        # Create an object to hold information about all the UAVs that
        # the server knows about
        self.uav_registry = UAVRegistry()

        # Import and configure the extensions that we want to use.
        self.extension_manager = ExtensionManager(self)
        self.extension_manager.configure(self.config.get("EXTENSIONS", {}))

    def _on_client_count_changed(self, sender):
        self.num_clients_changed.send(self)

    def _on_connection_state_changed(self, sender, entry, old_state,
                                     new_state):
        """Handler called when the state of a connection changes somewhere
        within the server. Dispatches an appropriate ``CONN-INF`` message.

        Parameters:
            sender (ConnectionRegistry): the connection registry
            entry (ConnectionEntry): a connection entry from the connection
                registry
            old_state (ConnectionState): the old state of the connection
            new_state (ConnectionState): the old state of the connection
        """
        if "socketio" in self.extensions:
            message = self.create_CONN_INF_message_for([entry.id])
            with self.app_context():
                self.message_hub.send_message(message)


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

        - Enum instances are converted to their names

        - Objects having a ``json`` property will be replaced by the value
          of this property

        Parameters:
            obj (object): the object to encode

        Returns:
            object: the JSON representation of the object
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Enum):
            return obj.name
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
    app.client_registry.add(request.sid)


@socketio.on("disconnect")
def handle_disconnection():
    """Handler called when a client disconnects from the server socket."""
    app.client_registry.remove(request.sid)


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


@app.message_hub.on("CONN-INF")
def handle_CONN_INF(message, hub):
    return app.create_CONN_INF_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("CONN-LIST")
def handle_CONN_LIST(message, hub):
    return {
        "ids": list(app.connection_registry.ids)
    }


@app.message_hub.on("SYS-VER")
def handle_SYS_VER(message, hub):
    return {
        "software": "flockwave-server",
        "version": server_version
    }


@app.message_hub.on("UAV-INF")
def handle_UAV_INF(message, hub):
    return app.create_UAV_INF_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message, hub):
    return {
        "ids": list(app.uav_registry.ids)
    }


# ######################################################################## #
