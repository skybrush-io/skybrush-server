from flockwave.server.model import Client, FlockwaveMessage
from typing import Callable, Optional

__all__ = ("RequestMiddleware", "ResponseMiddleware")


RequestMiddleware = Callable[[FlockwaveMessage, Client], Optional[FlockwaveMessage]]
"""Type specification for middleware functions that process incoming requests."""


ResponseMiddleware = Callable[
    [FlockwaveMessage, Optional[Client], Optional[FlockwaveMessage]],
    Optional[FlockwaveMessage],
]
"""Type specification for middleware functions that process outbound responses
and notifications.
"""
