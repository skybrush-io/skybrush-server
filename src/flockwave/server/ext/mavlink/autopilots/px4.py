from __future__ import annotations
from typing import TYPE_CHECKING

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.geofence import GeofenceConfigurationRequest, GeofenceStatus
from flockwave.server.model.safety import SafetyConfigurationRequest

from ..enums import MAVAutopilot, MAVModeFlag, MAVSysStatusSensor
from ..errors import UnknownFlightModeError
from ..types import MAVLinkFlightModeNumbers, MAVLinkMessage

from .base import Autopilot
from .registry import register_for_mavlink_type

if TYPE_CHECKING:
    from ..driver import MAVLinkUAV

__all__ = ("PX4",)


@register_for_mavlink_type(MAVAutopilot.PX4)
class PX4(Autopilot):
    """Class representing the PX4 autopilot firmware."""

    name = "PX4"

    #: Custom mode dictionary, containing the primary name and the aliases for
    #: each known main flight mode. The primary name should be from one of the
    #: constants in the FlightMode enum of the Flockwave spec
    _main_modes = {
        1: ("manual",),
        2: ("alt", "alt hold"),
        3: ("pos", "pos hold"),
        4: ("auto",),
        5: ("acro",),
        6: ("guided", "offboard"),
        7: ("stab", "stabilize"),
        8: ("rattitude",),
        9: ("simple",),
    }

    #: Custom mode dictionary, containing the primary name and the aliases for
    #: each known submode of the "auto" flight mode
    _auto_submodes = {
        2: ("takeoff",),
        3: ("loiter",),
        4: ("mission",),
        5: ("rth",),
        6: ("land",),
        8: ("follow",),
        9: ("precland",),
    }

    #: Mapping from mode names to the corresponding basemode / mode / submode
    #: triplets
    _mode_names_to_numbers = {
        "manual": (MAVModeFlag.CUSTOM_MODE_ENABLED, 1, 0),
        "althold": (MAVModeFlag.CUSTOM_MODE_ENABLED, 2, 0),
        "poshold": (MAVModeFlag.CUSTOM_MODE_ENABLED, 3, 0),
        "auto": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 0),
        "takeoff": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 2),
        "loiter": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 3),
        "mission": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 4),
        "rth": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 5),
        "rtl": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 5),
        "land": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 6),
        "follow": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 8),
        "precland": (MAVModeFlag.CUSTOM_MODE_ENABLED, 4, 9),
        "acro": (MAVModeFlag.CUSTOM_MODE_ENABLED, 5, 0),
        "guided": (MAVModeFlag.CUSTOM_MODE_ENABLED, 6, 0),
        "offboard": (MAVModeFlag.CUSTOM_MODE_ENABLED, 6, 0),
        "stab": (MAVModeFlag.CUSTOM_MODE_ENABLED, 7, 0),
        "stabilize": (MAVModeFlag.CUSTOM_MODE_ENABLED, 7, 0),
        "rattitude": (MAVModeFlag.CUSTOM_MODE_ENABLED, 8, 0),
    }

    @classmethod
    def describe_custom_mode(cls, base_mode: int, custom_mode: int) -> str:
        main_mode = (custom_mode & 0x00FF0000) >> 16
        submode = (custom_mode & 0xFF000000) >> 24
        main_mode_name = cls._main_modes.get(main_mode)

        if main_mode_name:
            main_mode_name = main_mode_name[0]

            if main_mode == 3:
                # "pos hold" has a "circle" submode
                if submode == 1:
                    return "circle"

            elif main_mode == 4:
                # ready (1), takeoff, loiter, mission, RTL, land, unused, follow,
                # precland
                submode_name = cls._auto_submodes.get(submode)
                if submode_name:
                    return submode_name[0]

            return main_mode_name

        else:
            submode = (custom_mode & 0xFF000000) >> 24
            return f"{main_mode:02X}{submode:02X}"

    def are_motor_outputs_disabled(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # It seems like PX4 is not reporting the status of the safety switch
        # anywhere
        return False

    def calibrate_accelerometer(self, uav: MAVLinkUAV):
        raise NotImplementedError

    def calibrate_compass(self, uav: MAVLinkUAV):
        raise NotImplementedError

    def can_handle_firmware_update_target(self, target_id: str) -> bool:
        return False

    async def configure_geofence(
        self, uav: MAVLinkUAV, configuration: GeofenceConfigurationRequest
    ) -> None:
        raise NotImplementedError

    async def configure_safety(
        self, uav: MAVLinkUAV, configuration: SafetyConfigurationRequest
    ) -> None:
        raise NotImplementedError

    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        mode = mode.lower().replace(" ", "")
        numbers = self._mode_names_to_numbers.get(mode)
        if numbers is None:
            raise UnknownFlightModeError(mode)

        return numbers

    async def get_geofence_status(self, uav: MAVLinkUAV) -> GeofenceStatus:
        raise NotImplementedError

    def handle_firmware_update(self, uav: MAVLinkUAV, target_id: str, blob: bytes):
        raise NotSupportedError

    @property
    def is_battery_percentage_reliable(self) -> bool:
        """Returns whether the autopilot provides reliable battery capacity
        percentages.
        """
        return True

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        mask = MAVSysStatusSensor.PREARM_CHECK.value
        if (
            sys_status.onboard_control_sensors_present
            & sys_status.onboard_control_sensors_enabled
            & mask
        ):
            return not bool(sys_status.onboard_control_sensors_health & mask)
        else:
            return False

    def is_prearm_error_message(self, text: str) -> bool:
        return text.startswith("Preflight ")

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        # base mode & 1 is "custom mode", 0x04 is the "auto" custom main mode,
        # 0x05 is the "rth" submode of the auto custom main mode
        return bool(base_mode & 1) and custom_mode & 0xFFFF0000 == 0x05040000

    def process_prearm_error_message(self, text: str) -> str:
        prefix, sep, suffix = text.partition(":")
        return suffix.strip() if sep else text

    @property
    def supports_local_frame(self) -> bool:
        # https://github.com/PX4/PX4-Autopilot/issues/10246
        return False

    @property
    def supports_mavftp_parameter_upload(self) -> bool:
        return False

    @property
    def supports_repositioning(self) -> bool:
        return True

    @property
    def supports_scheduled_takeoff(self):
        return False
