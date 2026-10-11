from __future__ import annotations

from flockwave.server.ext.mavlink.enums import (
    MAVType,
)

from .base import ArduPilot, FlightModeMap
from .registry import register_for_mavlink_vehicle_type

__all__ = ("ArduRover",)


@register_for_mavlink_vehicle_type(
    MAVType.GROUND_ROVER,
    MAVType.SURFACE_BOAT,
)
class ArduRover(ArduPilot):
    """Class representing the ArduRover firmware."""

    name = "ArduRover"

    _custom_modes: FlightModeMap = {
        0: ("manual",),
        1: ("acro",),
        3: ("steer", "steering"),
        4: ("hold",),
        5: ("loiter",),
        6: ("follow",),
        7: ("simple",),
        8: ("dock",),
        9: ("circle",),
        10: ("auto",),
        11: ("rth", "rtl", "return", "return to home", "return to launch"),
        12: ("srth", "srtl", "smart RTH", "smart RTL"),
        15: ("guided",),
        16: ("initialising",),
    }
    """ArduRover custom modes; see ardupilot/Rover/mode.h for reference"""

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & 1) and (custom_mode == 11 or custom_mode == 12)
