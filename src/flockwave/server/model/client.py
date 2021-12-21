"""Model classes related to a single client connected to the server."""

from dataclasses import dataclass
from trio import Event
from typing import Optional, Union

from flockwave.server.logger import log as base_log

from .channel import CommunicationChannel
from .user import User

__all__ = ("Client",)

log = base_log.getChild("model.clients")  # plural to match registry.clients


@dataclass(eq=False)
class Client:
    """A single client connected to the Flockwave server.

    Attributes:
        authenticated: signal that is sent when the client changes from an
            unauthenticated to an authenticated state
        deauthenticated: signal that is sent when the client changes from an
            authenticated to an unauthenticated state
    """

    _id: str
    _channel: CommunicationChannel
    _user: Optional[User] = None

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
    def user(self) -> Optional[User]:
        """The user that is authenticated on the communication channel that
        this client uses; `None` if the client is not authenticated yet.
        """
        return self._user

    @user.setter
    def user(self, value: Optional[Union[str, User]]) -> None:
        if value is not None and not isinstance(value, User):
            value = User.from_string(value)

        if value is self._user:
            return

        if self._user is not None:
            raise RuntimeError(
                "cannot re-authenticate a channel once it is already authenticated"
            )

        self._user = value

        if value:
            if hasattr(self, "_authenticated_event"):
                self._authenticated_event.set()
            log.info(f"Authenticated as {value}", extra={"id": self._id})
        else:
            log.info("Deauthenticated current user")

    async def wait_until_authenticated(self) -> None:
        """Waits until the client is authenticated."""
        if self.user is not None:
            return

        if not hasattr(self, "_authenticated_event"):
            self._authenticated_event = Event()

        try:
            await self._authenticated_event.wait()
        finally:
            del self._authenticated_event
