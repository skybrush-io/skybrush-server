"""Base model class that represents a communication channel between the
server and a connected client.

Concrete communication channel classes are typically implemented in
extensions (e.g., the ``socketio`` extension for Socket.IO communication
channels). The class in this package declares the base that the extensions
must extend.
"""

from abc import ABCMeta, abstractmethod

__all__ = ("CommunicationChannel",)


class CommunicationChannel(metaclass=ABCMeta):
    """Base model object representing a communication channel between the
    server and a client. Concrete implementations of this class are to be
    found in the appropriate Skybrush server extensions (e.g., the
    ``socketio`` extension for Socket.IO channels).
    """

    def __init__(self):
        """Constructor."""
        pass

    def bind_to(self, client):
        """Notifies the channel that it is communicating with the given
        client. Useful when the actual communication medium represented
        by this object is shared between multiple clients and the sending
        method has to know which client a message is intended to.

        Parameters:
            client (Client): the client to bind the channel to
        """
        pass

    async def close(self, force: bool = False):
        """Closes the server's endpoint of the channel.

        Parameters:
            force: whethr to attempt a forceful close
        """
        raise NotImplementedError

    @abstractmethod
    async def send(self, message):
        """Sends the given message over the communication channel."""
        raise NotImplementedError
