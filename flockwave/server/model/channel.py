"""Base model class that represents a communication channel between the
server and a connected client.

Concrete communication channel classes are typically implemented in
extensions (e.g., the ``socketio`` extension for Socket.IO communication
channels). The class in this package declares the base that the extensions
must extend.
"""

from __future__ import absolute_import

from abc import ABCMeta, abstractmethod
from future.utils import with_metaclass

__all__ = ("CommunicationChannel", )


class CommunicationChannel(with_metaclass(ABCMeta, object)):
    """Base model object representing a communication channel between the
    server and a client. Concrete implementations of this class are to be
    found in the appropriate Flockwave server extensions (e.g., the
    ``socketio`` extension for Socket.IO channels).
    """

    def __init__(self):
        """Constructor."""
        pass

    def bind_to(self):
        """Notifies the channel that it is communicating with the given
        client. Useful when the actual communication medium represented
        by this object is shared between multiple clients and the sending
        method has to know which client a message is intended to.

        Parameters:
            client (Client): the client to bind the channel to
        """
        pass

    @abstractmethod
    def send(self, message):
        """Sends the given message over the communication channel."""
        raise NotImplementedError