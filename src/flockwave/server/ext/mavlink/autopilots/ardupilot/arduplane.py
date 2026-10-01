from __future__ import annotations

import logging

from ...enums import (
    MAVModeFlag,
    MAVType,
)
from ..registry import register_for_mavlink_vehicle_type
from .base import ArduPilot, FlightModeMap

__all__ = ("ArduPlane",)

log = logging.getLogger(__name__)


@register_for_mavlink_vehicle_type(
    MAVType.FIXED_WING,
    MAVType.VTOL_TAILSITTER_DUOROTOR,
    MAVType.VTOL_TAILSITTER_QUADROTOR,
    MAVType.VTOL_TILTROTOR,
    # The MAV_TYPE values below are theoretically valid for ArduPlane, but
    # they cannot be set using the Q_MAV_TYPE parameter in ArduPilot 4.7,
    # so they are not registered here.
    # MAVType.VTOL_FIXEDROTOR,
    # MAVType.VTOL_TAILSITTER,
    # MAVType.VTOL_TILTWING,
    # MAVType.VTOL_RESERVED5,
)
class ArduPlane(ArduPilot):
    """Class representing the ArduPlane firmware."""

    name = "ArduPlane"

    _custom_modes: FlightModeMap = {
        0: ("manual",),
        1: ("circle",),
        2: ("stab", "stabilize"),
        3: ("training",),
        4: ("acro",),
        5: ("fbwa", "fly by wire a"),
        6: ("fbwb", "fly by wire b"),
        7: ("cruise",),
        8: ("autotune",),
        10: ("auto",),
        11: ("rtl", "rth", "return to launch"),
        12: ("loiter",),
        13: ("takeoff",),
        14: ("avoid ADSB", "avoid"),
        15: ("guided",),
        16: ("initialising", "init"),
        17: ("qstab", "qstabilize"),
        18: ("qhover",),
        19: ("qloiter",),
        20: ("qland",),
        21: ("qrtl",),
        22: ("qautotune",),
        23: ("qacro",),
        24: ("thermal",),
        25: ("loiter alt qland",),
        26: ("autoland",),
    }
    """ArduPlane custom modes (including QuadPlane VTOL modes);
    see ardupilot/ArduPlane/mode.h for reference"""

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & MAVModeFlag.CUSTOM_MODE_ENABLED) and custom_mode in [
            11,
            21,
        ]

    @property
    def supports_repositioning_with_explicit_altitude(self) -> bool:
        return True
