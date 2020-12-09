from enum import Enum
from typing import Optional

from flockwave.server.model.metamagic import ModelMeta
from flockwave.spec.schema import get_complex_object_schema

from .utils import enum_to_json


__all__ = ("LogMessage",)


class Severity(Enum):
    """Possible severity levels for a single log message."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogMessage(metaclass=ModelMeta):
    """Class representing a single log message that can be sent in a SYS-MSG
    message.
    """

    class __meta__:
        schema = get_complex_object_schema("logMessage")
        mappers = {"severity": enum_to_json(Severity)}

    def __init__(
        self,
        message: str,
        severity: Severity = Severity.INFO,
        sender: Optional[str] = None,
        timestamp: Optional[int] = None,
    ):
        self.message = message
        self.severity = severity
        if sender is not None:
            self.sender = sender
        if timestamp is not None:
            self.timestamp = timestamp
