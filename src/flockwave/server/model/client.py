"""Model classes related to a single client connected to the server."""

from __future__ import absolute_import

import attr

from typing import Union

from .channel import CommunicationChannel
from .user import User

__all__ = ("Client",)


@attr.s
class Client(object):
    """A single client connected to the Flockwave server."""

    _id: str = attr.ib()
    _channel: CommunicationChannel = attr.ib()
    _user: User = attr.ib(default=None)

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

    @property
    def user(self) -> User:
        """The user that is authenticated on the communication channel that
        this client uses.
        """
        return self._user

    @user.setter
    def user(self, value: Union[str, User]) -> None:
        if not isinstance(value, User):
            value = User.from_string(value)

        if self._user is not None:
            raise RuntimeError(
                "cannot re-authenticate a channel once it is already authenticated"
            )

        self._user = value
