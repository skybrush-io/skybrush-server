from flockwave.server.model import Client, FlockwaveMessage
from typing import Callable

__all__ = ("RequestMiddleware", "ResponseMiddleware")


RequestMiddleware = Callable[[FlockwaveMessage, Client], FlockwaveMessage | None]
"""Type specification for middleware functions that process incoming requests."""


ResponseMiddleware = Callable[
    [FlockwaveMessage, Client | None, FlockwaveMessage | None],
    FlockwaveMessage | None,
]
"""Type specification for middleware functions that process outbound responses
and notifications.
"""
