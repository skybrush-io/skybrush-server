"""Middleware that logs incoming and outgoing messages in the message hub."""

from logging import Logger

from flockwave.server.model import Client, FlockwaveMessage, FlockwaveNotification
from typing import ClassVar, Iterable, Optional, Set, Tuple

__all__ = ("RequestLogMiddleware",)


class RequestLogMiddleware:
    """Middleware that logs incoming requests in the message hub."""

    DEFAULT_EXCLUDED_MESSAGES: ClassVar[Tuple[str, ...]] = (
        "RTK-STAT",
        "UAV-PREFLT",
        "X-DBG-RESP",
        "X-RTK-STAT",
    )
    """Default set of excluded messages in this middleware."""

    exclude: Set[str]
    """Set of message types to exclude from the log."""

    log: Logger
    """Logger to log the messages to."""

    def __init__(
        self, log: Logger, *, exclude: Iterable[str] = DEFAULT_EXCLUDED_MESSAGES
    ):
        self.log = log
        self.exclude = set(exclude)

    def __call__(self, message: FlockwaveMessage, sender: Client) -> FlockwaveMessage:
        type = message.get_type() or "untyped"
        if type not in self.exclude:
            self.log.info(
                f"Received {type} message",
                extra={"id": message.id, "semantics": "request"},
            )
        return message


class ResponseLogMiddleware:
    """Middleware that logs outgoing responses, notifications and broadcasts
    in the message hub.
    """

    log: Logger
    """Logger to log the messages to."""

    def __init__(self, log: Logger):
        self.log = log

    def __call__(
        self,
        message: FlockwaveMessage,
        to: Optional[Client],
        in_response_to: Optional[FlockwaveMessage],
    ) -> FlockwaveMessage:
        type = message.get_type() or "untyped"
        if to is None:
            if type not in ("CONN-INF", "UAV-INF", "DEV-INF", "SYS-MSG", "X-DBG-REQ"):
                self.log.info(
                    f"Broadcasting {type} notification",
                    extra={"id": message.id, "semantics": "notification"},
                )
        elif in_response_to is not None:
            if type not in ("RTK-STAT", "X-RTK-STAT", "UAV-PREFLT"):
                self.log.info(
                    f"Sending {type} response",
                    extra={"id": in_response_to.id, "semantics": "response_success"},
                )
        elif isinstance(message, FlockwaveNotification):
            if type not in ("UAV-INF", "DEV-INF"):
                self.log.info(
                    f"Sending {type} notification",
                    extra={"id": message.id, "semantics": "notification"},
                )
        else:
            extra = {"semantics": "response_success"}
            if hasattr(message, "id"):
                extra["id"] = message.id

            self.log.info(f"Sending {type} message", extra=extra)

        return message
