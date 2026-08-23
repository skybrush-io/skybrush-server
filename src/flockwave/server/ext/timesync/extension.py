"""Core implementation of the `timesync` extension."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any, ContextManager, Protocol

from flockwave.server.ext.base import TypedConfigExtension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model.client import Client
from flockwave.server.model.log import Severity
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse

from .manager import TimeSource, TimeSyncManager
from .model import TimeSyncSnapshot
from .types import TimeSyncState

__all__ = (
    "TimeSource",
    "TimeSyncExtension",
    "TimeSyncExtensionAPI",
    "TimeSyncSnapshot",
    "TimeSyncState",
)


from .schema import TimeSyncConfig


class TimeSyncExtension(TypedConfigExtension[TimeSyncConfig]):
    """Extension that tracks server wall-clock synchronization status."""

    _manager: TimeSyncManager
    """Manager instance that stores the source state and computes snapshots."""

    def __init__(self):
        """Constructor."""
        super().__init__()
        self._manager = TimeSyncManager()

    def configure(self, configuration: TimeSyncConfig) -> None:
        """Updates the extension configuration from the extension settings."""
        super().configure(configuration)
        self._manager.configure(
            sync_threshold=configuration.sync_threshold,
            source_expiry_threshold=configuration.source_expiry_threshold,
            offset_log_threshold=configuration.offset_log_threshold,
            log=self.log,
        )

    def exports(self) -> dict[str, Any]:
        """Returns the API exposed by the extension to other extensions."""
        return {
            "get_status": self.get_status,
            "use_time_source": self.use_time_source,
        }

    def get_status(self) -> TimeSyncSnapshot:
        """Returns the current synchronization status snapshot."""
        return self._manager.get_status()

    def _create_timesync_status_message_body(
        self, snapshot: TimeSyncSnapshot | None = None
    ) -> dict[str, Any]:
        """Creates a message describing the current synchronization status."""
        snapshot = snapshot or self._manager.get_status()
        return {"type": "TIMESYNC-STATUS", "status": snapshot.json}

    def _handle_TIMESYNC_STATUS(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ) -> FlockwaveResponse:
        """Handles a request for the current synchronization status."""
        return hub.create_response_to(
            message, self._create_timesync_status_message_body()
        )

    def use_time_source(
        self, id: str, *, priority: int = 0
    ) -> ContextManager[TimeSource]:
        """Registers a time source for the duration of a context."""
        return self._manager.use_time_source(id, priority=priority)

    async def run(self) -> None:
        assert self.app is not None

        handler_map = {
            "TIMESYNC-STATUS": self._handle_TIMESYNC_STATUS,
        }

        with ExitStack() as stack:
            stack.enter_context(
                self._manager.sync_status_changed.connected_to(
                    self._maybe_send_status_change_message, sender=self._manager
                )
            )
            stack.enter_context(self.app.message_hub.use_message_handlers(handler_map))
            await self._manager.run()

    def _maybe_send_status_change_message(
        self, sender, current: TimeSyncSnapshot, previous: TimeSyncSnapshot
    ) -> None:
        # TODO(ntamas): also do this when a new client connects

        if not self.app:
            return

        if current.state == previous.state:
            return

        if current.state == "sync" and previous.state == "unknown":
            # Don't send a message when the server starts up and the first time source
            # reports a valid timestamp, because that is expected behavior.
            return

        message_hub = self.app.message_hub

        body = self._create_timesync_status_message_body(current)
        notification = message_hub.create_notification(body)
        self.app.message_hub.enqueue_broadcast_message(notification)

        send_message = self.app.request_to_send_SYS_MSG_message
        match current.state:
            case "sync":
                send_message("World clock and server clock are now in sync.")

            case "unsync":
                send_message(
                    "Server clock is not synchronized to world clock. Please sync "
                    "the date and time on the server to a reliable time source.",
                    severity=Severity.WARNING,
                )

            case "error":
                send_message(
                    "Cannot determine server clock synchronization state: all reporting "
                    "time sources indicated errors.",
                    severity=Severity.WARNING,
                )


class TimeSyncExtensionAPI(Protocol):
    """Interface specification for the API exposed by the `timesync` extension."""

    def get_status(self) -> TimeSyncSnapshot: ...
    def use_time_source(
        self, id: str, *, priority: int = 0
    ) -> ContextManager[TimeSource]: ...
