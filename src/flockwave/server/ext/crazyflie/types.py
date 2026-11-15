from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any

__all__ = ("ControllerType", "PositioningSystemType")


class ControllerType(IntEnum):
    """Enum representing the controller types used in the Crazyflie ecosystem."""

    AUTO_SELECT = 0
    PID = 1
    MELLINGER = 2
    INDI = 3
    BRESCIANINI = 4

    @classmethod
    def from_json(cls, value: Any) -> ControllerType | None:
        if value is None:
            return None

        if isinstance(value, str):
            value = value.lower()
            if value == "auto" or value == "autoselect":
                return cls.AUTO_SELECT
            elif value == "pid":
                return cls.PID
            elif value == "mellinger":
                return cls.MELLINGER
            elif value == "indi":
                return cls.INDI
            elif value == "brescianini":
                return cls.BRESCIANINI

        return cls(value)


class PositioningSystemType(Enum):
    """Enum representing the positioning systems that Crazyflie drones may
    use. All the drones managed by the extension are assumed to use the same
    positioning system.
    """

    UWB = "uwb"
    """Ultra-wideband anchors (Loco Positioning System)."""

    LIGHTHOUSE = "lighthouse"
    """Lighthouse base stations."""

    MOCAP = "mocap"
    """External motion capture system."""

    @classmethod
    def from_json(cls, value: Any) -> PositioningSystemType:
        if value is None:
            return cls.UWB

        if isinstance(value, str):
            value = value.lower()

        return cls(value)

    def describe(self) -> str:
        """Returns a human-readable description of the positioning system."""
        return _POSITIONING_SYSTEM_DESCRIPTIONS[self]


_POSITIONING_SYSTEM_DESCRIPTIONS = {
    PositioningSystemType.UWB: "Ultra-wideband anchors (Loco Positioning System)",
    PositioningSystemType.LIGHTHOUSE: "Lighthouse base stations",
    PositioningSystemType.MOCAP: "Motion capture system",
}
