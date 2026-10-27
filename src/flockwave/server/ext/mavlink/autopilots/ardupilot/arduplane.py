from __future__ import annotations

from typing import TYPE_CHECKING

from flockwave.server.ext.mavlink.enums import (
    MAVType,
)

from .base import ArduPilot, FlightModeMap
from .registry import register_for_mavlink_vehicle_type

if TYPE_CHECKING:
    from flockwave.server.ext.mavlink.driver import MAVLinkUAV

__all__ = ("ArduPlane", "ArduPlaneVTOLQuadrotor")


@register_for_mavlink_vehicle_type(
    MAVType.FIXED_WING,
    MAVType.VTOL_TAILSITTER_DUOROTOR,
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
        11: ("rth", "rtl", "return", "return to home", "return to launch"),
        12: ("loiter",),
        13: ("takeoff",),
        14: ("avoid ADSB", "avoid"),
        15: ("guided",),
        16: ("initialising", "init"),
        17: ("qstab", "qstabilize"),
        18: ("qhover",),
        19: ("qloiter",),
        20: ("qland",),
        21: ("qrth", "qrtl"),
        22: ("qautotune",),
        23: ("qacro",),
        24: ("thermal",),
        25: ("laqland", "loiter alt qland"),
        26: ("aland", "autoland"),
    }
    """ArduPlane custom modes (including QuadPlane VTOL modes);
    see ardupilot/ArduPlane/mode.h for reference"""

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & 1) and (custom_mode == 11 or custom_mode == 21)

    @property
    def supports_repositioning(self) -> bool:
        # Note that ArduPlane does not support MAV_CMD_DO_REPOSITION in its
        # full context as it still cannot handle NaN in its altitude argument
        # to reposition at the current altitude (4.7). However, ArduPlane does
        # not support fly to in guided mode either, so we just allow repositioning
        # to be able to handle fly_to() calls with explicit altitudes at least.
        return True


@register_for_mavlink_vehicle_type(MAVType.VTOL_TAILSITTER_QUADROTOR)
class ArduPlaneVTOLQuadrotor(ArduPlane):
    """Class representing the ArduPlane firmware on a VTOL quadrotor.

    Standard QuadPlanes report FIXED_WING by default, so they need Q_MAV_TYPE=20 to
    be handled by this class.
    """

    name = "ArduPlane VTOL quadrotor"

    async def takeoff(self, uav: MAVLinkUAV, *, channel: str) -> None:
        # By default VTOL quadrotors take off to a higher altitude than other UAVs
        await uav.takeoff_to_relative_altitude(50, channel=channel)
