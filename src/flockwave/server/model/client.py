"""Model classes related to a single client connected to the server."""

from __future__ import absolute_import

import attr

from .channel import CommunicationChannel
from .user import User

__all__ = ("Client",)


@attr.s
class Client(object):
    """A single client connected to the Flockwave server.

    Attributes:
        id: a unique identifier for the client
        channel: the communication channel that the client uses to connect to
            the server
        user: lightweight object providing information about the user that is
            authenticated on the communication channel that this client uses.
    """

    _id: str = attr.ib()
    _channel: CommunicationChannel = attr.ib()
    user: User = attr.ib(default=None)

    @property
    def channel(self) -> CommunicationChannel:
        """The communication channel that the client uses to connect to
        the server.
        """
        return self._channel

    @property
    def id(self) -> str:
        """A unique identifier for the client, assigned at construction
        time.
        """
        return self._id
