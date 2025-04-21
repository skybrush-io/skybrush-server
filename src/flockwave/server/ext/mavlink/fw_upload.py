"""Functions and objects related to uploading a firmware to a MAVLink-based
drone over a wireless connection.
"""

from enum import Enum

__all__ = ("FirmwareUpdateTarget", "FirmwareUpdateResult")


class FirmwareUpdateTarget(Enum):
    ABIN = "org.ardupilot.firmware.abin"

    def describe(self):
        """Returns a human-readable description of the target."""
        if self == FirmwareUpdateTarget.ABIN:
            return "ArduPilot ABIN firmware"
        return self.value


class FirmwareUpdateResult(Enum):
    """Result of a firmware update."""

    UNSUPPORTED = 0
    FAILED_TO_VERIFY = 1
    INVALID = 2
    FLASHING_FAILED = 3
    SUCCESS = 4

    def describe(self):
        """Returns a human-readable description of the result."""
        if self == FirmwareUpdateResult.UNSUPPORTED:
            return "Firmware update is not supported on this UAV"
        elif self == FirmwareUpdateResult.FAILED_TO_VERIFY:
            return "Failed to verify firmware update"
        elif self == FirmwareUpdateResult.INVALID:
            return "Firmware update is invalid"
        elif self == FirmwareUpdateResult.FLASHING_FAILED:
            return "Firmware update failed"
        elif self == FirmwareUpdateResult.SUCCESS:
            return "Firmware update completed successfully"

    @property
    def successful(self) -> bool:
        return self == FirmwareUpdateResult.SUCCESS
