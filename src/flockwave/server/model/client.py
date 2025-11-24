"""Model classes related to a single client connected to the server."""

from dataclasses import dataclass, field
from time import monotonic
from trio import Event

from flockwave.server.logger import log as base_log

from .channel import CommunicationChannel
from .user import User

__all__ = ("Client",)

log = base_log.getChild("model.clients")  # plural to match registry.clients


@dataclass(eq=False)
class Client:
    """A single client connected to the Flockwave server."""

    _id: str
    """Unique identifier for the client."""

    _channel: CommunicationChannel
    """The communication channel to use when sending messages to the client
    or receiving messages from it.
    """

    _user: User | None = None
    """The user that is authenticated on the communication channel that
    this client uses; `None` if the client is not authenticated yet.
    """

    _authenticated_event: Event | None = None
    """Event that is set when the client gets authenticated."""

    _connected_at: float = field(default_factory=monotonic)
    """Monotonic timestamp when the client connected; not related to actual
    wall-clock time.
    """

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
    def user(self) -> User | None:
        """The user that is authenticated on the communication channel that
        this client uses; `None` if the client is not authenticated yet.
        """
        return self._user

    @user.setter
    def user(self, value: str | User | None) -> None:
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
            if self._authenticated_event:
                self._authenticated_event.set()
            log.info(f"Authenticated as {value}", extra={"id": self._id})
        else:
            log.info("Deauthenticated current user")

    def get_connection_age_in_seconds(self, now: float) -> float:
        """The time elapsed since the connection was established, in seconds."""
        return (now if now is not None else monotonic()) - self._connected_at

    async def wait_until_authenticated(self) -> None:
        """Waits until the client is authenticated."""
        if self.user is not None:
            return

        if self._authenticated_event is None:
            self._authenticated_event = Event()

        try:
            await self._authenticated_event.wait()
        finally:
            del self._authenticated_event
