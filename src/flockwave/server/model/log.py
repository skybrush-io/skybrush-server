from base64 import b64encode
from enum import Enum
from typing import Any, Optional

from flockwave.server.model.metamagic import ModelMeta
from flockwave.spec.schema import get_complex_object_schema

from .utils import enum_to_json


__all__ = ("LogMessage", "FlightLog", "FlightLogKind", "FlightLogMetadata")


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


class FlightLogKind(Enum):
    """Supported flight log types."""

    UNKNOWN = "unknown"
    TEXT = "text"
    ARDUPILOT = "ardupilot"
    ULOG = "ulog"
    FLOCKCTRL = "flockctrl"

    def is_binary(self) -> bool:
        return self in (FlightLogKind.ARDUPILOT, FlightLogKind.ULOG)


class FlightLogMetadata(metaclass=ModelMeta):
    """Class representing the metadata of a flight log of a UAV that can be
    sent in a LOG-INF message.
    """

    class __meta__:
        schema = get_complex_object_schema("flightLogMetadata")
        mappers = {"kind": enum_to_json(FlightLogKind)}

    id: str
    kind: FlightLogKind = FlightLogKind.UNKNOWN
    size: Optional[int] = None
    timestamp: Optional[int] = None

    @classmethod
    def create(
        cls,
        id: str,
        kind: FlightLogKind = FlightLogKind.UNKNOWN,
        size: Optional[int] = None,
        timestamp: Optional[int] = None,
    ):
        result = cls()
        result.id = str(id)
        result.kind = kind
        result.size = size
        result.timestamp = timestamp
        return result


class FlightLog(metaclass=ModelMeta):
    """Class representing a flight log of a UAV that can be sent in a
    LOG-DATA message.
    """

    class __meta__:
        schema = get_complex_object_schema("flightLog")
        mappers = {"kind": enum_to_json(FlightLogKind)}

    id: str
    kind: FlightLogKind = FlightLogKind.UNKNOWN
    size: Optional[int] = None
    timestamp: Optional[int] = None
    body: Any

    @classmethod
    def create(
        cls,
        id: str,
        kind: FlightLogKind = FlightLogKind.UNKNOWN,
        body: Any = "",
        size: Optional[int] = None,
        timestamp: Optional[int] = None,
    ):
        result = cls()
        result.id = str(id)
        result.kind = kind
        result.body = body
        result.size = size
        result.timestamp = timestamp

        if size is None and kind is FlightLogKind.TEXT and isinstance(body, str):
            result.size = len(body)

        return result

    @classmethod
    def create_from_metadata(cls, metadata: FlightLogMetadata, body: Any = ""):
        encoded_body = (
            b64encode(body).decode("ascii")
            if isinstance(body, bytes) and metadata.kind.is_binary
            else body
        )
        return cls.create(
            id=metadata.id,
            kind=metadata.kind,
            size=len(body) if isinstance(body, (str, bytes)) else metadata.size,
            timestamp=metadata.timestamp,
            body=encoded_body,
        )

    def get_metadata(self) -> FlightLogMetadata:
        """Converts the log object into its metadata only."""
        return FlightLogMetadata.create(
            id=self.id, kind=self.kind, size=self.size, timestamp=self.timestamp
        )
