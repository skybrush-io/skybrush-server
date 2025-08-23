from __future__ import annotations
from typing import TYPE_CHECKING

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.geofence import GeofenceConfigurationRequest, GeofenceStatus
from flockwave.server.model.safety import SafetyConfigurationRequest

from ..types import MAVLinkFlightModeNumbers, MAVLinkMessage

from .base import Autopilot

if TYPE_CHECKING:
    from ..driver import MAVLinkUAV

__all__ = ("UnknownAutopilot",)


class UnknownAutopilot(Autopilot):
    """Class representing an autopilot that we do not know."""

    name = "Unknown autopilot"

    def calibrate_accelerometer(self, uav: MAVLinkUAV):
        raise NotSupportedError

    def calibrate_compass(self, uav: MAVLinkUAV):
        raise NotSupportedError

    def can_handle_firmware_update_target(self, target_id: str) -> bool:
        return False

    async def configure_geofence(
        self, uav: MAVLinkUAV, configuration: GeofenceConfigurationRequest
    ) -> None:
        raise NotSupportedError

    async def configure_safety(
        self, uav: MAVLinkUAV, configuration: SafetyConfigurationRequest
    ) -> None:
        raise NotSupportedError

    def are_motor_outputs_disabled(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        return False

    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        raise NotSupportedError

    async def get_geofence_status(self, uav: MAVLinkUAV) -> GeofenceStatus:
        raise NotSupportedError

    def handle_firmware_update(self, uav: MAVLinkUAV, target_id: str, blob: bytes):
        raise NotSupportedError

    @property
    def is_battery_percentage_reliable(self) -> bool:
        # Let's be optimistic :)
        return True

    def is_prearm_error_message(self, text: str) -> bool:
        return False

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        return False

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return False

    @property
    def supports_local_frame(self) -> bool:
        # Let's be pessimistic :(
        return False

    @property
    def supports_mavftp_parameter_upload(self) -> bool:
        return False

    @property
    def supports_repositioning(self) -> bool:
        return False

    @property
    def supports_scheduled_takeoff(self):
        return False
