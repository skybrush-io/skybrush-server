"""Error classes specific to the MAVLink extension."""

from typing import Optional, Union

from .enums import MAVMissionResult

__all__ = (
    "MAVLinkExtensionError",
    "UnknownFlightModeError",
    "InvalidSigningKeyError",
    "InvalidSystemIdError",
    "MissionAcknowledgmentError",
)


class MAVLinkExtensionError(RuntimeError):
    """Base class for all error classes related to the MAVLink extension."""

    pass


class UnknownFlightModeError(MAVLinkExtensionError):
    """Error thrown when the driver cannot decode a flight mode name to a
    base mode / custom mode configuration.
    """

    def __init__(self, mode: Union[str, int], message: Optional[str] = None):
        message = message or f"Unknown flight mode: {mode!r}"
        super().__init__(message)


class InvalidSigningKeyError(MAVLinkExtensionError):
    """Error thrown when there is a problem with a MAVLink signing key."""

    pass


class InvalidSystemIdError(MAVLinkExtensionError):
    """Error thrown when a system ID is invalid or outside an allowed range."""

    def __init__(self, system_id: int, message: Optional[str] = None):
        message = message or f"Invalid system ID: {system_id!r}"
        super().__init__(message)


class MissionAcknowledgmentError(MAVLinkExtensionError):
    """Error thrown when a mission item operation fails with a non-success
    MAV_MISSION_RESULT value.
    """

    result: int
    operation: Optional[str]

    def __init__(self, result: int, operation: Optional[str] = None):
        self.result = result
        self.operation = operation

        operation = (
            f"MAVLink mission operation ({operation})"
            if operation
            else "MAVLink mission operation"
        )
        message = f"{operation} returned {MAVMissionResult.describe(result)}"
        super().__init__(message)
