"""Message hub for unit tests that records outbound messages instead of sending
them to clients.

The `hub` fixture in ``conftest.py`` provides an instance of this class to the
tests; import the class from this module only if you need it for type
annotations or for creating additional instances.
"""

from typing import Any

from flockwave.server.message_hub import MessageHub, Request
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveNotification

__all__ = ("RecordingMessageHub",)


class RecordingMessageHub(MessageHub):
    """Message hub that records outbound messages instead of sending them.

    Messages addressed to a single client are recorded in `sent`; broadcast
    messages are recorded in `broadcasts`. Messages are recorded as soon as they
    are enqueued or sent, and they are recorded by their bodies.
    """

    broadcasts: list[dict[str, Any]]
    """Bodies of the messages that were broadcast to all clients."""

    sent: list[tuple[Client | str, dict[str, Any]]]
    """Recipients and bodies of the messages that were addressed to a single
    client.
    """

    def __init__(self):
        super().__init__()
        self.broadcasts = []
        self.sent = []

    def events_sent_to(self, client: Client) -> list[list[dict[str, Any]]]:
        """Returns the events that were sent to the given client in `EVT-INF`
        notifications, grouped by notification.
        """
        return [
            body["events"]
            for to, body in self.sent
            if to is client and body.get("type") == "EVT-INF"
        ]

    async def broadcast_message(self, message: FlockwaveNotification) -> Request:
        self.enqueue_broadcast_message(message)
        return Request(message)

    def enqueue_broadcast_message(self, message: FlockwaveNotification) -> None:
        self.broadcasts.append(dict(message.body))

    def enqueue_message(
        self,
        message: FlockwaveMessage | dict[str, Any],
        to: str | Client | None = None,
        in_response_to: FlockwaveMessage | None = None,
    ) -> None:
        if not isinstance(message, FlockwaveMessage):
            message = self.create_response_or_notification(
                message, in_response_to=in_response_to
            )

        if to is None:
            assert isinstance(message, FlockwaveNotification)
            self.enqueue_broadcast_message(message)
        else:
            self.sent.append((to, dict(message.body)))

    async def send_message(
        self,
        message: FlockwaveMessage | dict[str, Any],
        to: str | Client | None = None,
        in_response_to: FlockwaveMessage | None = None,
    ) -> Request:
        if not isinstance(message, FlockwaveMessage):
            message = self.create_response_or_notification(
                message, in_response_to=in_response_to
            )
        self.enqueue_message(message, to=to, in_response_to=in_response_to)
        return Request(message, to=to, in_response_to=in_response_to)
