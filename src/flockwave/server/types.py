"""Type aliases used in multiple places throughout the server."""

from __future__ import annotations

from typing import Any, Callable, Protocol

from flockwave.server.model.log import Severity

__all__ = ("Disposer",)


Disposer = Callable[[], Any]
"""Type specification for disposer functions that can be called with no arguments
to get rid of something registered earlier.
"""


class GCSLogMessageSender(Protocol):
    """Type specification for functions that can be used to send a log message
    to the GCS from a UAV, with a given severity.
    """

    def __call__(self, message: str, *, severity: Severity = Severity.INFO): ...
