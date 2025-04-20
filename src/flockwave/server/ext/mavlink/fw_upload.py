"""Functions and objects related to uploading a firmware to a MAVLink-based
drone over a wireless connection.
"""

from enum import Enum

__all__ = ("FirmwareUpdateTarget",)


class FirmwareUpdateTarget(Enum):
    ABIN = "org.ardupilot.firmware.abin"
