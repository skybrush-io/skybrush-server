"""Extension that allows the server to provide status information to
`skybrush-console-frontend` running on a Raspberry Pi or a similar
device.
"""

import os
import platform

from contextlib import ExitStack
from logging import Logger
from json import dumps
from trio import open_memory_channel, MemorySendChannel, WouldBlock
from trio.abc import ReceiveChannel
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from flockwave.connections import ConnectionState
from flockwave.server.registries import ConnectionRegistry, ConnectionRegistryEntry
from flockwave.server.utils import overridden

from .base import Extension

if TYPE_CHECKING:
    from trio.lowlevel import FdStream  # not available on Windows


#: Dictionary mapping connection statuses to corresponding string representations
_status_to_string = {
    ConnectionState.CONNECTED: "connected",
    ConnectionState.CONNECTING: "connecting",
    ConnectionState.DISCONNECTED: "disconnected",
    ConnectionState.DISCONNECTING: "disconnecting",
}


class ConsoleStatusExtension(Extension):
    """Extension that allows the server to provide status information to
    `skybrush-console-frontend` running on a Raspberry Pi or a similar
    device.
    """

    log: Logger

    _queue_tx: Optional[MemorySendChannel]
    _stream: Optional["FdStream"]

    def __init__(self):
        super().__init__()

        self._queue_tx = None
        self._stream = None

    async def run(self, app, configuration, log) -> None:
        fd = _get_fd_to_console_frontend()
        if fd is None:
            return

        if platform.system() == "Windows":
            log.warn("Extension not supported on this platform")
            return

        # Lazy import -- FdStream not available on Windows
        from trio.lowlevel import FdStream

        with ExitStack() as stack:
            connection_registry = app.connection_registry

            stack.enter_context(
                connection_registry.added.connected_to(
                    self._on_connection_added, sender=connection_registry
                )
            )
            stack.enter_context(
                connection_registry.removed.connected_to(
                    self._on_connection_removed, sender=connection_registry
                )
            )
            stack.enter_context(
                connection_registry.connection_state_changed.connected_to(
                    self._on_connection_state_changed, sender=connection_registry
                )
            )

            queue_tx, queue_rx = open_memory_channel(128)
            stack.enter_context(overridden(self, _queue_tx=queue_tx))

            try:
                async with FdStream(fd) as self._stream:
                    await self._run(queue_rx)
            finally:
                self._stream = None

    async def _run(self, queue_rx: ReceiveChannel) -> None:
        await self._send_full_status_information()
        async with queue_rx:
            async for item in queue_rx:
                await self._send_status_information(item)

    def _enqueue_status_information(self, info: List[Tuple[str, Any]]) -> None:
        """Enqueues a new piece of status information to be sent to the
        frontend as soon as possible.
        """
        if not self._queue_tx:
            # Extension not running any more so we are not interested
            return

        try:
            self._queue_tx.send_nowait(info)
        except WouldBlock:
            self.log.info("Dropped status information addressed to console frontend")

    def _get_full_status_information(self) -> List[Tuple[str, Any]]:
        """Gathers the full status information to send from the application
        object and returns it as a list of key-value pairs.
        """
        assert self.app is not None
        items = [
            self._get_status_information_for_entry(entry)
            for entry in self.app.connection_registry
        ]
        items.sort()
        return items

    def _get_status_information_for_entry(
        self, entry: ConnectionRegistryEntry, deleted: bool = False
    ) -> Tuple[str, Any]:
        assert entry.connection is not None
        status = _status_to_string.get(entry.connection.state, "unknown")
        return (f"Connections|{entry.description}", None if deleted else status)

    def _on_connection_added(
        self, sender: ConnectionRegistry, entry: ConnectionRegistryEntry
    ) -> None:
        """Handler called when a connection is added to the connection registry.

        Sends a partial state information object containing the state of the
        newly added connection.

        Parameters:
            sender: the connection registry
            entry: the connection that was added
        """
        update = [self._get_status_information_for_entry(entry)]
        self._enqueue_status_information(update)

    def _on_connection_removed(
        self, sender: ConnectionRegistry, entry: ConnectionRegistryEntry
    ) -> None:
        """Handler called when a connection is removed from the connection
        registry.

        Sends a partial state information object containing the state of the
        newly removed connection (i.e. the fact that it was removed).

        Parameters:
            sender: the connection registry
            entry: the connection that was removed
        """
        update = [self._get_status_information_for_entry(entry, deleted=True)]
        self._enqueue_status_information(update)

    def _on_connection_state_changed(
        self,
        sender: ConnectionRegistry,
        entry: ConnectionRegistryEntry,
        old_state: ConnectionState,
        new_state: ConnectionState,
    ) -> None:
        """Handler called when the state of a connection in the connection
        registry changes.

        Sends a partial state information object containing the new state of the
        changed connection.

        Parameters:
            sender: the connection registry
            entry: the connection that changed
        """
        update = [self._get_status_information_for_entry(entry)]
        self._enqueue_status_information(update)

    async def _send_full_status_information(self) -> None:
        """Sends the full status information to the console frontend."""
        return await self._send_status_information(self._get_full_status_information())

    async def _send_status_information(self, obj) -> None:
        """Sends the given status information object to the console frontend."""
        if not obj or not self._stream:
            return

        message = dumps({"type": "status", "args": [obj]}).encode("utf-8") + b"\n"
        await self._stream.send_all(message)


def _get_fd_to_console_frontend() -> Optional[int]:
    """Returns the file descriptor that we should use to commnicate with the
    console frontend, or `None` if we were not launched by the console
    frontend.
    """
    try:
        return int(os.environ.get("SB_CONSOLE_FRONTEND_STATUS_FD"))  # type: ignore
    except Exception:
        return None


construct = ConsoleStatusExtension
description = "Status information module for the console-based frontend"
schema = {}
