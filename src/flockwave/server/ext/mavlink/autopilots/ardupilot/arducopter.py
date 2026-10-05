from __future__ import annotations

from flockwave.server.ext.mavlink.autopilots.registry import (
    register_for_mavlink_vehicle_type,
)
from flockwave.server.ext.mavlink.enums import (
    MAVModeFlag,
    MAVProtocolCapability,
    MAVState,
    MAVSysStatusSensor,
    MAVType,
)
from flockwave.server.ext.mavlink.types import MAVLinkMessage

from .base import ArduPilot, FlightModeMap, extend_custom_modes

__all__ = (
    "ArduCopter",
    "ArduCopterWithSkybrush",
)


@register_for_mavlink_vehicle_type(
    MAVType.QUADROTOR,
    MAVType.COAXIAL,
    MAVType.HELICOPTER,
    MAVType.HEXAROTOR,
    MAVType.OCTOROTOR,
    MAVType.TRICOPTER,
    MAVType.DECAROTOR,
    MAVType.DODECAROTOR,
)
class ArduCopter(ArduPilot):
    """Class representing the ArduCopter firmware."""

    name = "ArduCopter"

    _custom_modes: FlightModeMap = {
        0: ("stab", "stabilize"),
        1: ("acro",),
        2: ("alt", "alt hold"),
        3: ("auto",),
        4: ("guided",),
        5: ("loiter",),
        6: ("rth",),
        7: ("circle",),
        9: ("land",),
        11: ("drift",),
        13: ("sport",),
        14: ("flip",),
        15: ("tune",),
        16: ("pos", "pos hold"),
        17: ("brake",),
        18: ("throw",),
        19: ("avoid ADSB", "avoid"),
        20: ("guided no GPS",),
        21: ("smart RTH",),
        22: ("flow", "flow hold"),
        23: ("follow",),
        24: ("zigzag",),
        25: ("system ID",),
        26: ("heli autorotate", "autorotate"),
        27: ("auto RTH",),
        28: ("turtle",),
    }
    """ArduCopter custom modes; see ardupilot/ArduCopter/mode.h for reference"""

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & MAVModeFlag.CUSTOM_MODE_ENABLED) and custom_mode in [
            6,
            21,
        ]

    def refine_with_capabilities(self, capabilities: int):
        result = super().refine_with_capabilities(capabilities)

        if isinstance(result, self.__class__) and not isinstance(
            result, ArduCopterWithSkybrush
        ):
            mask = ArduCopterWithSkybrush.CAPABILITY_MASK
            if (capabilities & mask) == mask:
                result = ArduCopterWithSkybrush(self)

        return result


class ArduCopterWithSkybrush(ArduCopter):
    """Class representing the ArduCopter firmware with Skybrush-specific
    extensions to support drone shows.
    """

    name = "ArduCopter + Skybrush"

    _custom_modes = extend_custom_modes(ArduCopter._custom_modes, {127: ("show",)})

    CAPABILITY_MASK = (
        MAVProtocolCapability.PARAM_FLOAT
        | MAVProtocolCapability.FTP
        | MAVProtocolCapability.SET_POSITION_TARGET_GLOBAL_INT
        | MAVProtocolCapability.SET_POSITION_TARGET_LOCAL_NED
        | MAVProtocolCapability.MAVLINK2
        | MAVProtocolCapability.DRONE_SHOW_MODE
    )

    def is_duplicate_message(self, message: MAVLinkMessage) -> bool:
        # We use the MSB of the compatibility flags to indicate that the message
        # is semantically equivalent to an earlier message of the same type from
        # the same source
        return message.get_header().compat_flags & 0x80

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # Our patched firmware (ab)uses the CALIBRATING state in the heartbeat
        # for this before ArduCopter 4.0.5. From ArduCopter 4.0.5 onwwards,
        # there is a "preflight check" sensor so we use that
        mask = MAVSysStatusSensor.PREARM_CHECK.value
        if sys_status.onboard_control_sensors_present & mask:
            # ArduCopter version reports prearm check status with this message
            if sys_status.onboard_control_sensors_enabled & mask:
                # Prearm checks are enabled so return whether they pass or not
                return not bool(sys_status.onboard_control_sensors_health & mask)
            else:
                # Prearm checks are disabled so they are never in progress
                return False
        else:
            # ArduCopter version does not know about this flag so we assume that
            # we are running our firmware and that the CALIBRATING status is
            # used for reporting this
            return heartbeat.system_status == MAVState.CALIBRATING

    @ArduPilot.supports_scheduled_takeoff.getter
    def supports_scheduled_takeoff(self):
        return True
