"""Implementations of autopilot-specific functionality."""

from abc import ABCMeta, abstractmethod, abstractproperty
from trio import fail_after, sleep, TooSlowError
from typing import Dict, Type, Union

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.geofence import (
    GeofenceAction,
    GeofenceConfigurationRequest,
    GeofenceStatus,
)
from flockwave.server.utils import clamp

from .enums import (
    MAVAutopilot,
    MAVCommand,
    MAVMessageType,
    MAVModeFlag,
    MAVParamType,
    MAVProtocolCapability,
    MAVState,
    MAVSysStatusSensor,
)
from .errors import UnknownFlightModeError
from .geofence import GeofenceManager
from .types import MAVLinkFlightModeNumbers, MAVLinkMessage
from .utils import (
    decode_param_from_wire_representation,
    encode_param_to_wire_representation,
    log_id_for_uav,
)


class Autopilot(metaclass=ABCMeta):
    """Interface specification and generic entry point for autopilot objects."""

    name = "Abstract autopilot"

    def __init__(self, base=None) -> None:
        self.capabilities = int(getattr(base, "capabilities", 0))

    @staticmethod
    def from_autopilot_type(type: int) -> Type["Autopilot"]:
        """Returns an autopilot class suitable to represent the behaviour of
        an autopilot with the given MAVLink autopilot identifier in the
        heartbeat message.
        """
        return _autopilot_registry.get(type, UnknownAutopilot)

    @classmethod
    def from_heartbeat(cls, message: MAVLinkMessage) -> Type["Autopilot"]:
        """Returns an autopilot class suitable to represent the behaviour of
        an autopilot with the given MAVLink heartbeat message.
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
        raise NotImplementedError

    @abstractmethod
    async def calibrate_compass(self, uav) -> None:
        """Calibrates the compass of the UAV.

        Raises:
            NotImplementedError: if we have not implemented support for
                calibrating the compass (but it supports compass calibration)
            NotSupportedError: if the autopilot does not support calibrating the
                compass
        """
        raise NotImplementedError

    @abstractmethod
    async def configure_geofence(
        self, uav, configuration: GeofenceConfigurationRequest
    ) -> None:
        """Updates the geofence configuration on the autopilot to match the
        given configuration object.

        Raises:
            NotImplementedError: if we have not implemented support for updating
                the geofence configuration on the autopilot (but it supports
                geofences)
            NotSupportedError: if the autopilot does not support updating the
                geofence
        """
        raise NotImplementedError

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
        raise NotImplementedError

    @abstractmethod
    async def get_geofence_status(self, uav) -> GeofenceStatus:
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
        raise NotImplementedError

    @abstractproperty
    def is_battery_percentage_reliable(self) -> bool:
        """Returns whether the autopilot provides reliable battery capacity
        percentages.
        """
        raise NotImplementedError

    @abstractmethod
    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        """Decides whether the prearm check is still in progress on the UAV,
        assuming that this information is reported either in the heartbeat or
        the SYS_STATUS message.
        """
        raise NotImplementedError

    @abstractmethod
    def is_prearm_error_message(self, text: str) -> bool:
        """Returns whether the given text from a MAVLink STATUSTEXT message
        indicates a prearm check error.
        """
        raise NotImplementedError

    @abstractmethod
    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        """Decides whether the flight mode identified by the given base and
        custom mode numbers is a return-to-home mode.
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

    @abstractproperty
    def supports_local_frame(self) -> bool:
        """Returns whether the autopilot understands MAVLink commands sent in
        a local coordinate frame.
        """
        raise NotImplementedError

    @abstractproperty
    def supports_repositioning(self) -> bool:
        """Returns whether the autopilot understands the MAVLink MAV_CMD_DO_REPOSITION
        command.
        """
        raise NotImplementedError

    @abstractproperty
    def supports_scheduled_takeoff(self) -> bool:
        """Returns whether the autopilot supports scheduled takeoffs."""
        raise NotImplementedError


class UnknownAutopilot(Autopilot):
    """Class representing an autopilot that we do not know."""

    name = "Unknown autopilot"

    async def calibrate_compass(self, uav) -> None:
        raise NotSupportedError

    async def configure_geofence(
        self, uav, configuration: GeofenceConfigurationRequest
    ) -> None:
        raise NotSupportedError

    def are_motor_outputs_disabled(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        return False

    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        raise NotSupportedError

    async def get_geofence_status(self, uav) -> GeofenceStatus:
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
    def supports_repositioning(self) -> bool:
        return False

    @property
    def supports_scheduled_takeoff(self):
        return False


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

    async def calibrate_compass(self, uav) -> None:
        raise NotImplementedError

    async def configure_geofence(
        self, uav, configuration: GeofenceConfigurationRequest
    ) -> None:
        raise NotImplementedError

    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        mode = mode.lower().replace(" ", "")
        numbers = self._mode_names_to_numbers.get(mode)
        if numbers is None:
            raise UnknownFlightModeError(mode)

        return numbers

    async def get_geofence_status(self, uav) -> GeofenceStatus:
        raise NotImplementedError

    @property
    def is_battery_percentage_reliable(self) -> bool:
        """Returns whether the autopilot provides reliable battery capacity
        percentages.
        """
        # TODO(ntamas): PX4 is actually much better at it than ArduPilot;
        # switch this to True once the user can configure on the UI whether
        # he wants to see percentages or voltages
        return False

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        mask = MAVSysStatusSensor.PREARM_CHECK
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
    def supports_repositioning(self) -> bool:
        return True

    @property
    def supports_scheduled_takeoff(self):
        return False


class ArduPilot(Autopilot):
    """Class representing the ArduPilot autopilot firmware."""

    name = "ArduPilot"

    #: Custom mode dictionary, containing the primary name and the aliases for
    #: each known flight mode
    _custom_modes = {
        0: ("stab", "stabilize"),
        1: ("acro",),
        2: (
            "alt",
            "alt hold",
        ),
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
        16: (
            "pos",
            "pos hold",
        ),
        17: ("brake",),
        18: ("throw",),
        19: ("avoid ADSB", "avoid"),
        20: ("guided no GPS",),
        21: ("smart RTH",),
        22: (
            "flow",
            "flow hold",
        ),
        23: ("follow",),
        24: ("zigzag",),
        25: ("system ID",),
        26: ("heli autorotate", "autorotate"),
        27: ("auto RTH",),
        28: ("turtle",),
    }

    _geofence_actions = {
        0: (GeofenceAction.REPORT,),
        1: (GeofenceAction.RETURN, GeofenceAction.LAND),
        2: (GeofenceAction.LAND,),
        3: (GeofenceAction.SMART_RETURN, GeofenceAction.RETURN, GeofenceAction.LAND),
        4: (GeofenceAction.STOP, GeofenceAction.LAND),
    }

    #: Maximum allowed duration of a compass calibration, in seconds
    MAX_COMPASS_CALIBRATION_DURATION = 60

    @classmethod
    def describe_custom_mode(cls, base_mode: int, custom_mode: int) -> str:
        """Returns the description of the current custom mode that the autopilot
        is in, given the base and the custom mode in the heartbeat message.

        This method is called if the "custom mode" bit is set in the base mode
        of the heartbeat.
        """
        mode_attrs = cls._custom_modes.get(custom_mode)
        return mode_attrs[0] if mode_attrs else f"mode {custom_mode}"

    def are_motor_outputs_disabled(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # ArduPilot uses the MOTOR_OUTPUTS "sensor" to indicate whether the
        # motor outputs are disabled. More precisely, it always has
        # MOTOR_OUTPUTS in the "present" and "health" field and the "enabled"
        # field specifies whether the motor outputs are enabled
        if (
            sys_status.onboard_control_sensors_health & MAVSysStatusSensor.MOTOR_OUTPUTS
            and sys_status.onboard_control_sensors_present
            & MAVSysStatusSensor.MOTOR_OUTPUTS
        ):
            return not bool(
                sys_status.onboard_control_sensors_enabled
                & MAVSysStatusSensor.MOTOR_OUTPUTS
            )
        else:
            return False

    async def calibrate_compass(self, uav) -> None:
        calibration_messages = {
            MAVMessageType.MAG_CAL_PROGRESS: 1,
            MAVMessageType.MAG_CAL_REPORT: 1,
        }
        started, success = False, False
        timeout = self.MAX_COMPASS_CALIBRATION_DURATION

        try:
            async with uav.temporarily_request_messages(calibration_messages):
                # Messages are not handled here but in the MAVLinkNetwork,
                # which forwards them to the UAV< which in turn refreshes its
                # state variables in its CompassCalibration object. This is not
                # nice, but it works.
                await uav.driver.send_command_long(
                    uav,
                    MAVCommand.DO_START_MAG_CAL,
                    0,  # calibrate all compasses
                    0,  # retry on failure
                    1,  # autosave on success
                )
                started = True

                # We give ourselves 60 seconds to do the compass calibration.
                # Anything that goes slower than 60 seconds probably indicates a
                # problem with the compass of the UAV.
                with fail_after(timeout):
                    success = await uav.compass_calibration.wait_until_termination()

        except TooSlowError:
            raise RuntimeError(
                f"Compass calibration did not finish in {timeout} seconds"
            )

        except Exception:
            if not started:
                raise
                raise RuntimeError("Failed to start compass calibration")
            else:
                try:
                    await uav.driver.send_command_long(
                        uav, MAVCommand.DO_CANCEL_MAG_CAL
                    )
                except Exception:
                    uav.driver.log.warn(
                        "Failed to cancel compass calibration",
                        extra={"id": log_id_for_uav(uav)},
                    )
                raise RuntimeError("Compass calibration terminated unexpectedly")

        if not success:
            raise RuntimeError("Compass calibration failed")

        # Wait a bit so the user sees the LED flashes on the drone that indicate a
        # successful calibration
        await sleep(1.5)

        try:
            await uav.reboot()
        except Exception:
            raise RuntimeError(
                "Failed to reboot UAV after successful compass calibration"
            )

    async def configure_geofence(
        self, uav, configuration: GeofenceConfigurationRequest
    ) -> None:
        if configuration.min_altitude is not None:
            # Update the minimum altitude limit; note that ArduCopter supports
            # only the [-100; 100] range.
            min_altitude = float(clamp(configuration.min_altitude, -100, 100))
            await uav.set_parameter("FENCE_ALT_MIN", min_altitude)

        if configuration.max_altitude is not None:
            # Update the maximum altitude limit; note that ArduCopter supports
            # only the [10; 1000] range.
            max_altitude = float(clamp(configuration.max_altitude, 10, 1000))
            await uav.set_parameter("FENCE_ALT_MAX", max_altitude)

        if configuration.max_distance is not None:
            # Update the maximum distance; note that ArduCopter supports only
            # the [30; 10000] range.
            max_altitude = float(clamp(configuration.max_distance, 30, 10000))
            await uav.set_parameter("FENCE_RADIUS", max_altitude)

        if configuration.enabled is not None:
            # Update whether the fence is enabled or disabled
            await uav.set_parameter("FENCE_ENABLE", int(bool(configuration.enabled)))

        if configuration.polygons is not None:
            # Generic stuff comes here
            manager = GeofenceManager.for_uav(uav)
            await manager.set_geofence_areas(configuration.polygons)

        if configuration.rally_points is not None:
            # TODO(ntamas): update rally points
            pass

    def decode_param_from_wire_representation(
        self, value: Union[int, float], type: MAVParamType
    ) -> float:
        # ArduCopter does not implement the MAVLink specification correctly and
        # requires all parameter values to be sent as floats, no matter what
        # their type is. See this link from Gitter:
        #
        # https://gitter.im/ArduPilot/pymavlink?at=5bfb975587c4b86bcc1af3ee
        return float(value)

    def encode_param_to_wire_representation(
        self, value: Union[int, float], type: MAVParamType
    ) -> float:
        # ArduCopter does not implement the MAVLink specification correctly and
        # requires all parameter values to be sent as floats, no matter what
        # their type is. See this link from Gitter:
        #
        # https://gitter.im/ArduPilot/pymavlink?at=5bfb975587c4b86bcc1af3ee
        return float(value)

    def get_flight_mode_numbers(self, mode: str) -> MAVLinkFlightModeNumbers:
        mode = mode.lower().replace(" ", "")
        for number, names in self._custom_modes.items():
            for name in names:
                name = name.lower().replace(" ", "")
                if name == mode:
                    return (MAVModeFlag.CUSTOM_MODE_ENABLED, number, 0)

        raise UnknownFlightModeError(mode)

    async def get_geofence_status(self, uav) -> GeofenceStatus:
        status = GeofenceStatus()

        # Generic stuff comes here
        manager = GeofenceManager.for_uav(uav)
        await manager.get_geofence_areas_and_rally_points(status)

        # ArduCopter-specific parameters are used to extend the status
        value = await uav.get_parameter("FENCE_ENABLE")
        status.enabled = bool(value)

        value = await uav.get_parameter("FENCE_ALT_MIN")
        status.min_altitude = float(value)

        value = await uav.get_parameter("FENCE_ALT_MAX")
        status.max_altitude = float(value)

        value = await uav.get_parameter("FENCE_RADIUS")
        status.max_distance = float(value)

        value = await uav.get_parameter("FENCE_ACTION")
        status.actions = list(self._geofence_actions.get(value, ()))

        return status

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # Information not reported by ArduPilot by default
        return False

    def is_prearm_error_message(self, text: str) -> bool:
        return text.startswith("PreArm: ")

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & 1) and (custom_mode == 6 or custom_mode == 21)

    def process_prearm_error_message(self, text: str) -> str:
        return text[8:]

    def refine_with_capabilities(self, capabilities: int):
        result = super().refine_with_capabilities(capabilities)

        if isinstance(result, self.__class__) and not isinstance(
            result, ArduPilotWithSkybrush
        ):
            mask = ArduPilotWithSkybrush.CAPABILITY_MASK
            if (capabilities & mask) == mask:
                result = ArduPilotWithSkybrush(self)

        return result

    @property
    def is_battery_percentage_reliable(self) -> bool:
        # The battery percentage estimate of the stock ArduPilot is broken;
        # it is based on discharged current only so it always reports a
        # newly inserted battery as fully charged
        return False

    @property
    def supports_local_frame(self) -> bool:
        return True

    @property
    def supports_repositioning(self) -> bool:
        return False

    @property
    def supports_scheduled_takeoff(self):
        return False


def extend_custom_modes(super, _new_modes, **kwds):
    """Helper function to extend the custom modes of an Autopilot_ subclass
    with new modes.
    """
    result = dict(super._custom_modes)
    result.update(_new_modes)
    result.update(**kwds)
    return result


class ArduPilotWithSkybrush(ArduPilot):
    """Class representing the ArduCopter firmware with Skybrush-specific
    extensions to support drone shows.
    """

    name = "ArduPilot + Skybrush"
    _custom_modes = extend_custom_modes(ArduPilot, {127: ("show",)})

    CAPABILITY_MASK = (
        MAVProtocolCapability.PARAM_FLOAT
        | MAVProtocolCapability.FTP
        | MAVProtocolCapability.SET_POSITION_TARGET_GLOBAL_INT
        | MAVProtocolCapability.SET_POSITION_TARGET_LOCAL_NED
        | MAVProtocolCapability.MAVLINK2
        | MAVProtocolCapability.DRONE_SHOW_MODE
    )

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # Our patched firmware (ab)uses the CALIBRATING state in the heartbeat
        # for this before ArduCopter 4.0.5. From ArduCopter 4.0.5 onwwards,
        # there is a "preflight check" sensor so we use that
        mask = MAVSysStatusSensor.PREARM_CHECK
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


_autopilot_registry: Dict[int, Type[Autopilot]] = {
    MAVAutopilot.ARDUPILOTMEGA: ArduPilot,
    MAVAutopilot.PX4: PX4,
}
