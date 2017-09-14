"""Base model class that represents a communication channel between the
server and a connected client.

Concrete communication channel classes are typically implemented in
extensions (e.g., the ``socketio`` extension for Socket.IO communication
channels). The class in this package declares the base that the extensions
must extend.
"""

from __future__ import absolute_import

__all__ = ("CommunicationChannel", )


class CommunicationChannel(object):
    """Base model object representing a communication channel between the
    server and a client. Concrete implementations of this class are to be
    found in the appropriate Flockwave server extensions (e.g., the
    ``socketio`` extension for Socket.IO channels).
    """

    def __init__(self):
        """Constructor."""
        pass
