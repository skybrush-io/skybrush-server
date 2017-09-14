"""Extension that provides Socket.IO communication channels for the server.

This extension enables the server to communicate with clients using
Socket.IO connections.
"""

from ..model import CommunicationChannel


class SocketIOChannel(CommunicationChannel):
    """Object that represents a Socket.IO communication channel between a
    server and a single client.
    """

    def __init__(self):
        """Constructor."""
        pass


def load(app, configuration, logger):
    """Loads the extension."""
    app.channel_type_registry.add("sio", SocketIOChannel)


def unload(app, configuration):
    """Unloads the extension."""
    app.channel_type_registry.remove(SocketIOChannel)
