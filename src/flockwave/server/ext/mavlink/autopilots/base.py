from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional, Union, Type

from flockwave.server.model.commands import Progress, ProgressEventsWithSuspension
from flockwave.server.model.geofence import (
    GeofenceConfigurationRequest,
    GeofenceStatus,
)
from flockwave.server.model.safety import SafetyConfigurationRequest

from ..enums import MAVParamType
from ..types import MAVLinkFlightModeNumbers, MAVLinkMessage
from ..utils import (
    decode_param_from_wire_representation,
    encode_param_to_wire_representation,
)

if TYPE_CHECKING:
    from ..driver import MAVLinkUAV

__all__ = ("Autopilot",)


class Autopilot(ABC):
    """Interface specification and generic entry point for autopilot objects."""

    name = "Abstract autopilot"
    capabilities: int = 0

    def __init__(self, base: Optional[Autopilot] = None) -> None:
        self.capabilities = int(getattr(base, "capabilities", 0))

    @staticmethod
    def from_autopilot_type(type: int) -> Type["Autopilot"]:
        """Returns an autopilot factory that can construct an Autopilot_
        instance that is suitable to represent the behaviour of an autopilot
        with the given MAVLink autopilot identifier.
        """
        from .registry import get_autopilot_factory_by_mavlink_type

        return get_autopilot_factory_by_mavlink_type(type)

    @classmethod
    def from_heartbeat(cls, message: MAVLinkMessage) -> Type["Autopilot"]:
        """Returns an autopilot factory that can construct an Autopilot_
        instance that is suitable to represent the behaviour of an autopilot
        that sent the given MAVLink heartbeat message.
        """
        return cls.from_autopilot_type(message.autopilot)

    @classmethod
    def describe_mode(cls, base_mode: int, custom_mode: int) -> str:
        """Returns the description of the current mode that the autopilot is
        in, given the base and the custom mode in the heartbeat message.
        """
        if base_mode & 1:
            # custom mode
            return cls.describe_custom_mode(base_mode, custom_mode)
        elif base_mode & 4:
            # auto mode
            return "auto"
        elif base_mode & 8:
            # guided mode
            return "guided"
        elif base_mode & 16:
            # stabilize mode
            return "stabilize"
        elif base_mode & 64:
            # manual mode
            return "manual"
        else:
            # anything else
            return "unknown"

    @classmethod
    def describe_custom_mode(cls, base_mode: int, custom_mode: int) -> str:
        """Returns the description of the current custom mode that the autopilot
        is in, given the base and the custom mode in the heartbeat message.

        This method is called if the "custom mode" bit is set in the base mode
        of the heartbeat.
        """
        return f"mode {custom_mode}"

    @abstractmethod
    def are_motor_outputs_disabled(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        """Decides whether the motor outputs of a UAV with this autopilot are
        disabled, given the MAVLink HEARTBEAT and SYS_STATUS messages where this
        information is conveyed for _some_ autopilots.
        """
        ...

    @abstractmethod
    def calibrate_accelerometer(
        self, uav: MAVLinkUAV
    ) -> ProgressEventsWithSuspension[None, str]:
        """Calibrates the accelerometers of the UAV.

        Yields:
            events describing the progress of the calibration

        Raises:
            NotImplementedError: if we have not implemented support for
                calibrating the accelerometers (but it supports accelerometer
                calibration)
            NotSupportedError: if the autopilot does not support accelerometer
                calibration
        """
        ...

    @abstractmethod
    def calibrate_compass(
        self, uav: MAVLinkUAV
    ) -> ProgressEventsWithSuspension[None, str]:
        """Calibrates the compasses of the UAV.

        Yields:
            events describing the progress of the calibration

        Raises:
            NotImplementedError: if we have not implemented support for
                calibrating compasses (but it supports compass calibration)
            NotSupportedError: if the autopilot does not support compass
                calibration
        """
        ...

    @abstractmethod
    def can_handle_firmware_update_target(self, target_id: str) -> bool:
        """Returns whether the UAV can handle firmware uploads with the given
        target.
        """
        ...

    @abstractmethod
    async def configure_geofence(
        self, uav: MAVLinkUAV, configuration: GeofenceConfigurationRequest
    ) -> None:
        """Updates the geofence configuration on the autopilot to match the
        given configuration object.

        Raises:
            NotImplementedError: if we have not implemented support for updating
                the geofence configuration on the autopilot (but it supports
                geofences)
            NotSupportedError: if the autopilot does not support updating the
                geofence or if the configuration request contains something that
                the drone is not capable of doing (e.g., smart landing on a
                drone that does not support collective collision avoidance)
        """
        ...

    @abstractmethod
    async def configure_safety(
        self, uav: MAVLinkUAV, configuration: SafetyConfigurationRequest
    ) -> None:
        """Updates the safety configuration on the autopilot to match the
        given configuration object.

        Raises:
            NotImplementedError: if we have not implemented support for updating
                the safety configuration on the autopilot (but it supports
                safety features)
            NotSupportedError: if the autopilot does not support updating the
                safety or if the configuration request contains something that
                the drone is not capable of doing
        """
        ...

    def decode_param_from_wire_representation(
        self, value: Union[int, float], type: MAVParamType
    ) -> float:
        """Decodes the given MAVLink parameter value returned from a MAVLink
        PARAM_VALUE message into its "real" value as a float.
        """
        return decode_param_from_wire_representation(value, type)

    def encode_param_to_wire_representation(
        self, value: Union[int, float], type: MAVParamType
    ) -> float:
        """Encodes the given MAVLink parameter value as a float suitable to be
        transmitted over the wire in a MAVLink PARAM_SET command.
        """
        return encode_param_to_wire_representation(value, type)

    @abstractmethod
    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        """Returns the numeric flight modes (mode, custom mode, custom submode)
        corresponding to the given mode description as a string.

        Raises:
            NotImplementedError: if we have not implemented the conversion from
                a mode string to a flight mode number set
            UnknownFlightModeError: if the flight mode is not known to the autopilot
        """
        ...

    @abstractmethod
    async def get_geofence_status(self, uav: MAVLinkUAV) -> GeofenceStatus:
        """Retrieves a full geofence status object from the drone.

        Parameters:
            uav: the MAVLinkUAV object

        Returns:
            a full geofence status object

        Raises:
            NotImplementedError: if we have not implemented support for retrieving
                the geofence status from the autopilot (but it supports
                geofences)
            NotSupportedError: if the autopilot does not support geofences at all
        """
        ...

    @abstractmethod
    def handle_firmware_update(
        self, uav: MAVLinkUAV, target_id: str, blob: bytes
    ) -> AsyncIterator[Progress]:
        """Handles a firmware update request on the UAV.

        This function is called only when the UAV is known to be able to handle
        a firmware update with the given target ID.

        Args:
            target_id: the target ID of the firmware update
            blob: the firmware update blob

        Yields:
            Progress_ objects to indicate the progress of the firmware update

        Raises:
            RuntimeError: if there was an error during the firmware update
            NotImplementedError: if we have not implemented support for
                firmware updates (but we plan to do so)
            NotSupportedError: if the autopilot does not support firmware
                updates
        """
        ...

    @property
    @abstractmethod
    def is_battery_percentage_reliable(self) -> bool:
        """Returns whether the autopilot provides reliable battery capacity
        percentages.
        """
        ...

    @abstractmethod
    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        """Decides whether the prearm check is still in progress on the UAV,
        assuming that this information is reported either in the heartbeat or
        the SYS_STATUS message.
        """
        ...

    @abstractmethod
    def is_prearm_error_message(self, text: str) -> bool:
        """Returns whether the given text from a MAVLink STATUSTEXT message
        indicates a prearm check error.
        """
        ...

    @abstractmethod
    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        """Decides whether the flight mode identified by the given base and
        custom mode numbers is a return-to-home mode.
        """
        ...

    def prepare_mavftp_parameter_upload(
        self, parameters: dict[str, float]
    ) -> tuple[str, bytes]:
        """Prepares a MAVFTP bulk parameter upload if the autopilot supports it.

        This function must be called only if `self.supports_mavftp_parameter_upload()`
        returns `True`, otherwise it must raise a `NotImplementedError`.

        The default implementation raises a `NotImplementedError` unconditionally.
        """
        raise NotImplementedError

    def process_prearm_error_message(self, text: str) -> str:
        """Preprocesses a prearm error from a MAVLInk STATUSTEXT message,
        identified earlier with `is_prearm_error_message()`, before it is fed
        into the preflight check subsystem in the server. May be used to strip
        unneeded prefixes from the message.

        The default implementation returns the message as is.
        """
        return text

    def refine_with_capabilities(self, capabilities: int):
        """Refines the autopilot class with further information from the
        capabilities bitfield of the MAVLink "autopilot capabilities" message,
        returning a new autopilot instance if the autopilot type can be narrowed
        further by looking at the capabilities.
        """
        self.capabilities = capabilities
        return self

    @property
    @abstractmethod
    def supports_local_frame(self) -> bool:
        """Returns whether the autopilot understands MAVLink commands sent in
        a local coordinate frame.
        """
        ...

    @property
    @abstractmethod
    def supports_mavftp_parameter_upload(self) -> bool:
        """Returns whether the autopilot supports uploading parameters via the
        MAVFTP protocol.
        """
        ...

    @property
    @abstractmethod
    def supports_repositioning(self) -> bool:
        """Returns whether the autopilot understands the MAVLink MAV_CMD_DO_REPOSITION
        command.
        """
        ...

    @property
    @abstractmethod
    def supports_scheduled_takeoff(self) -> bool:
        """Returns whether the autopilot supports scheduled takeoffs."""
        ...
