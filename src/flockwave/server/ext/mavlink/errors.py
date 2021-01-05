"""Error classes specific to the MAVLink extension."""

from typing import Optional, Union

__all__ = ("MAVLinkExtensionError", "UnknownFlightModeError")


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
