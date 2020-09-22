"""Stuff that is not in the official ``aiocflib`` Crazyflie library and that is
related to our extensions that we added to the Crazyflie firmware.
"""

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from struct import Struct
from typing import Optional, Tuple

from aiocflib.crtp.crtpstack import CRTPPort

__all__ = (
    "DRONE_SHOW_PORT",
    "LIGHT_PROGRAM_MEMORY_ID",
    "PREFLIGHT_STATUS_LIGHT_EFFECT",
    "DroneShowCommand",
    "DroneShowStatus",
)


#: Constant representing the CRTP port where we can access droneshow-related
#: services on the Crazyflie if it is running our patched firmware
DRONE_SHOW_PORT = CRTPPort.UNUSED_1

#: ID of the memory on the Crazyflie that can be used to store light programs
LIGHT_PROGRAM_MEMORY_ID = 0x18

#: LED ring effect type index that belongs to our preflight checks
PREFLIGHT_STATUS_LIGHT_EFFECT = 0x13


class DroneShowCommand(IntEnum):
    """Enum representing the possible command codes we can send to the
    Crazyflie drone show port.
    """

    START = 0
    PAUSE = 1
    STOP = 2
    STATUS = 3
    DEFINE_LIGHT_PROGRAM = 4


class LightProgramLocation(IntEnum):
    """Location codes for light programs."""

    INVALID = 0
    MEM = 1


class LightProgramType(IntEnum):
    """Encoding types for light programs."""

    RGB = 0
    RGB565 = 1
    SKYBRUSH = 2


class PreflightCheckStatus(IntEnum):
    """Enum representing the possible results of an onboard preflight check."""

    OFF = 0
    FAIL = 1
    WAIT = 2
    PASS = 3


class DroneShowStatusFlag(IntFlag):
    """Flags for the status bits of the drone show status packet."""

    BATTERY_CHARGING = 1
    HIGH_LEVEL_COMMANDER_ENABLED = 2
    DRONE_SHOW_MODE_ENABLED = 4


@dataclass
class DroneShowStatus:
    """Data class representing the response to a `DroneShowCommand.STATUS`
    command.
    """

    battery_voltage: float = 0.0
    flags: int = 0
    preflight_checks: Tuple[PreflightCheckStatus, ...] = ()
    position: Optional[Tuple[float, float, float]] = None
    light_status: int = 0

    _struct = Struct("<HhhhH")

    @property
    def battery_percentage(self) -> int:
        """Returns the approximate battery charge percentage."""
        percentage = round(100 * (self.battery_voltage - 3.1) / 1.1)
        return min(max(percentage, 0), 100)

    @property
    def charging(self) -> bool:
        """Returns whether the battery is charging."""
        return self.flags & DroneShowStatusFlag.BATTERY_CHARGING

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw response to the
        `DroneShowCommand.STATUS` command.
        """
        checks, x, y, z, light_status = cls._struct.unpack(data[3:13])
        checks = tuple((checks >> (index * 2)) & 0x03 for index in range(8))
        return cls(
            battery_voltage=data[1] / 10.0,
            flags=data[2],
            preflight_checks=checks,
            position=(x / 1000.0, y / 1000.0, z / 1000.0),
            light_status=light_status,
        )

    def has_flag(self, flag: DroneShowStatusFlag) -> bool:
        """Returns whether the status object has the given flag."""
        return bool(self.flags & flag)

    @property
    def mode(self) -> str:
        """Returns a flight mode code that can be inferred from the status
        packet.
        """
        if self.flags & DroneShowStatusFlag.DRONE_SHOW_MODE_ENABLED:
            return "show"
        elif self.flags & DroneShowStatusFlag.HIGH_LEVEL_COMMANDER_ENABLED:
            return "hcmd"
        else:
            return "----"
