"""Model classes related to a single client connected to the server."""

from __future__ import absolute_import

__all__ = ("Client",)


class Client(object):
    """A single client connected to the Flockwave server."""

    def __init__(self, id, channel):
        """Constructor.

        Parameters:
            id (str): a unique identifier for the client
            channel (CommunicationChannel): the communication channel that
                the client uses to connect to the server
        """
        self._id = id
        self._channel = channel

    @property
    def channel(self):
        """The communication channel that the client uses to connect to
        the server.
        """
        return self._channel

    @property
    def id(self):
        """A unique identifier for the client, assigned at construction
        time.
        """
        return self._id
