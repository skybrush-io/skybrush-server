from __future__ import annotations

from contextlib import aclosing
from dataclasses import dataclass
from functools import partial
from io import BytesIO
from struct import Struct
from time import monotonic
from trio import sleep, TooSlowError
from typing import IO, AsyncIterator, Iterable, Sequence, Union, TYPE_CHECKING, cast

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.commands import (
    Progress,
    ProgressEventsWithSuspension,
    Suspend,
)
from flockwave.server.model.geofence import (
    GeofenceAction,
    GeofenceConfigurationRequest,
    GeofenceStatus,
)
from flockwave.server.model.safety import (
    LowBatteryThresholdType,
    SafetyConfigurationRequest,
)
from flockwave.server.utils import clamp

from ..enums import (
    MAVAutopilot,
    MAVCommand,
    MAVMessageType,
    MAVModeFlag,
    MAVParamType,
    MAVProtocolCapability,
    MAVState,
    MAVSysStatusSensor,
)
from ..errors import UnknownFlightModeError
from ..ftp import MAVFTP
from ..fw_upload import FirmwareUpdateResult, FirmwareUpdateTarget
from ..geofence import GeofenceManager, GeofenceType
from ..types import MAVLinkFlightModeNumbers, MAVLinkMessage
from ..utils import log_id_for_uav

if TYPE_CHECKING:
    from ..driver import MAVLinkUAV

from .base import Autopilot
from .registry import register_for_mavlink_type

__all__ = ("ArduPilot", "ArduPilotWithSkybrush")


@register_for_mavlink_type(MAVAutopilot.ARDUPILOTMEGA)
class ArduPilot(Autopilot):
    """Class representing the ArduPilot autopilot firmware."""

    name = "ArduPilot"

    #: Custom mode dictionary, containing the primary name and the aliases for
    #: each known flight mode
    _custom_modes = {
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

    _geofence_actions = {
        0: (GeofenceAction.REPORT,),
        1: (GeofenceAction.RETURN, GeofenceAction.LAND),
        2: (GeofenceAction.LAND,),
        3: (GeofenceAction.SMART_RETURN, GeofenceAction.RETURN, GeofenceAction.LAND),
        4: (GeofenceAction.STOP, GeofenceAction.LAND),
    }

    MAX_ACCELEROMETER_CALIBRATION_DURATION = 120
    """Maximum allowed duration of an accelerometer calibration, in seconds"""

    MAX_COMPASS_CALIBRATION_DURATION = 60
    """Maximum allowed duration of a compass calibration, in seconds"""

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
            sys_status.onboard_control_sensors_health
            & MAVSysStatusSensor.MOTOR_OUTPUTS.value
            and sys_status.onboard_control_sensors_present
            & MAVSysStatusSensor.MOTOR_OUTPUTS.value
        ):
            return not bool(
                sys_status.onboard_control_sensors_enabled
                & MAVSysStatusSensor.MOTOR_OUTPUTS.value
            )
        else:
            return False

    async def calibrate_accelerometer(
        self, uav: MAVLinkUAV
    ) -> ProgressEventsWithSuspension[None, str]:
        # Reset our internal state object of the accelerometer calibration procedure
        uav.accelerometer_calibration.reset()

        # accelerometer calibration starts with sending a proper preflight
        # calib command
        success = await uav.driver.send_command_long(
            uav, MAVCommand.PREFLIGHT_CALIBRATION, 0, 0, 0, 0, 1
        )
        if not success:
            raise RuntimeError("Failed to start accelerometer calibration")

        successful_calibration = False
        timeout = self.MAX_ACCELEROMETER_CALIBRATION_DURATION

        try:
            async for progress in uav.accelerometer_calibration.updates(
                timeout=timeout, fail_on_timeout=False
            ):
                yield progress
                if isinstance(progress, Suspend):
                    # Accel calibration was suspended, but then we got here, so
                    # the user must have resumed the operation. Let's forward
                    # the resume instruction to the UAV.
                    success = await uav.driver.send_command_long(
                        uav,
                        MAVCommand.ACCELCAL_VEHICLE_POS,
                        uav.accelerometer_calibration.next_step,
                    )
                    if not success:
                        raise RuntimeError("Failed to resume accelerometer calibration")

                    uav.accelerometer_calibration.notify_resumed()
                elif isinstance(progress, Progress):
                    if progress.percentage == 100:
                        successful_calibration = True

        except TooSlowError:
            raise RuntimeError(
                f"Accelerometer calibration did not finish in {timeout} seconds"
            ) from None

        if successful_calibration:
            # Indicate to the user that we are now rebooting the drone, otherwise
            # it's confusing that the UI shows 100% but the operation is still in
            # progress
            yield Progress.done("Rebooting...")

            # Wait a bit so the user sees the LED flashes on the drone that indicate a
            # successful calibration
            await sleep(1.5)

            try:
                await uav.reboot()
            except Exception:
                raise RuntimeError(
                    "Failed to reboot UAV after successful accelerometer calibration"
                ) from None

        yield Progress.done("Acceelerometer calibration successful.")

    async def calibrate_compass(
        self, uav: MAVLinkUAV
    ) -> ProgressEventsWithSuspension[None, str]:
        calibration_messages = {
            int(MAVMessageType.MAG_CAL_PROGRESS): 1.0,
            int(MAVMessageType.MAG_CAL_REPORT): 1.0,
        }
        started, success = False, False
        timeout = self.MAX_COMPASS_CALIBRATION_DURATION

        try:
            async with uav.temporarily_request_messages(calibration_messages):
                # Reset our internal state object of the compass calibration procedure
                uav.compass_calibration.reset()

                # Messages are not handled here but in the MAVLinkNetwork,
                # which forwards them to the UAV, which in turn refreshes its
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

                async for progress in uav.compass_calibration.updates(
                    timeout=timeout, fail_on_timeout=False
                ):
                    if isinstance(progress, Progress):
                        if progress.percentage == 100:
                            success = True
                    yield progress

        except TooSlowError:
            raise RuntimeError(
                f"Compass calibration did not finish in {timeout} seconds"
            ) from None

        except RuntimeError:
            raise

        except Exception:
            if not started:
                raise RuntimeError("Failed to start compass calibration") from None
            try:
                await uav.driver.send_command_long(uav, MAVCommand.DO_CANCEL_MAG_CAL)
            except Exception:
                uav.driver.log.warning(
                    "Failed to cancel compass calibration",
                    extra={"id": log_id_for_uav(uav)},
                )
            raise RuntimeError("Compass calibration terminated unexpectedly") from None

        if success:
            # Indicate to the user that we are now rebooting the drone, otherwise
            # it's confusing that the UI shows 100% but the operation is still in
            # progress
            yield Progress.done("Rebooting...")

            # Wait a bit so the user sees the LED flashes on the drone that indicate a
            # successful calibration
            await sleep(1.5)

            try:
                await uav.reboot()
            except Exception:
                raise RuntimeError(
                    "Failed to reboot UAV after successful compass calibration"
                ) from None

        yield Progress.done("Compass calibration successful.")

    def can_handle_firmware_update_target(self, target_id: str) -> bool:
        return target_id == FirmwareUpdateTarget.ABIN.value

    async def configure_geofence(
        self, uav: MAVLinkUAV, configuration: GeofenceConfigurationRequest
    ) -> None:
        fence_type = GeofenceType.OFF

        if configuration.min_altitude is not None:
            # Update the minimum altitude limit; note that ArduCopter supports
            # only the [-100; 100] range.
            min_altitude = float(clamp(configuration.min_altitude, -100, 100))
            await uav.set_parameter("FENCE_ALT_MIN", min_altitude)
            fence_type |= GeofenceType.FLOOR
        else:
            # Assume that the minimum altitude limit is disabled
            pass

        if configuration.max_altitude is not None:
            # Update the maximum altitude limit; note that ArduCopter supports
            # only the [10; 1000] range.
            max_altitude = float(clamp(configuration.max_altitude, 10, 1000))
            await uav.set_parameter("FENCE_ALT_MAX", max_altitude)
            fence_type |= GeofenceType.ALTITUDE
        else:
            # Assume that the maximum altitude limit is disabled
            pass

        if configuration.max_distance is not None:
            # Update the maximum distance; note that ArduCopter supports only
            # the [30; 10000] range.
            max_distance = float(clamp(configuration.max_distance, 30, 10000))
            await uav.set_parameter("FENCE_RADIUS", max_distance)
            fence_type |= GeofenceType.CIRCLE
        else:
            # Assume that the distance limit is disabled
            pass

        if configuration.polygons is not None:
            # Update geofence polygons
            manager = GeofenceManager.for_uav(uav)
            await manager.set_geofence_areas(configuration.polygons)
            fence_type |= GeofenceType.POLYGON
        else:
            # Assume that the polygon fence is disabled
            pass

        if configuration.rally_points is not None:
            if configuration.rally_points:
                raise NotImplementedError("rally points not supported yet")

        if configuration.action is not None:
            # Update geofence action
            action_map = {
                GeofenceAction.LAND: 2,  # always land
                GeofenceAction.REPORT: 0,  # report only
                GeofenceAction.RETURN: 1,  # RTH or land
                GeofenceAction.STOP: 4,  # brake or land
            }
            mapped_action = action_map.get(configuration.action)
            if mapped_action is not None:
                await uav.set_parameter("FENCE_ACTION", int(mapped_action))
            else:
                raise NotSupportedError(
                    f"geofence action {configuration.action!r} not supported on ArduPilot"
                )
        else:
            # Assume that we do not need to change the geofence action
            pass

        # Update the type of the geofence
        await uav.set_parameter("FENCE_TYPE", int(fence_type))

        if configuration.enabled is None:
            # Infer whether the fence should be enabled or disabled based on
            # fence_type
            fence_enabled = bool(fence_type)
        else:
            fence_enabled = bool(configuration.enabled)

        # Update whether the fence is enabled or disabled
        await uav.set_parameter("FENCE_ENABLE", int(fence_enabled))

    async def configure_safety(
        self, uav, configuration: SafetyConfigurationRequest
    ) -> None:
        if configuration.low_battery_threshold is not None:
            if configuration.low_battery_threshold.type == LowBatteryThresholdType.OFF:
                await uav.set_parameter("BATT_LOW_MAH", 0)
                await uav.set_parameter("BATT_LOW_VOLT", 0)
            elif (
                configuration.low_battery_threshold.type
                == LowBatteryThresholdType.VOLTAGE
            ):
                await uav.set_parameter(
                    "BATT_LOW_VOLT", configuration.low_battery_threshold.value
                )
                await uav.set_parameter("BATT_LOW_MAH", 0)
            elif (
                configuration.low_battery_threshold.type
                == LowBatteryThresholdType.PERCENTAGE
            ):
                capacity = await uav.get_parameter("BATT_CAPACITY")
                await uav.set_parameter(
                    "BATT_LOW_MAH",
                    capacity * configuration.low_battery_threshold.value / 100,
                )
                await uav.set_parameter("BATT_LOW_VOLT", 0)
            else:
                raise RuntimeError(
                    f"Unknown low battery threshold type: {configuration.low_battery_threshold.type!r}"
                )

        if configuration.critical_battery_voltage is not None:
            await uav.set_parameter(
                "BATT_CRT_VOLT", configuration.critical_battery_voltage
            )
        if configuration.return_to_home_altitude is not None:
            await uav.set_parameter(
                "RTL_ALT",
                int(configuration.return_to_home_altitude * 100),  # [m] -> [cm]
            )
        if configuration.return_to_home_speed is not None:
            await uav.set_parameter(
                "RTL_SPEED",
                int(configuration.return_to_home_speed * 100),  # [m/s] -> [cm/s]
            )

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

    async def get_geofence_status(self, uav: MAVLinkUAV) -> GeofenceStatus:
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
        status.actions = list(self._geofence_actions.get(int(value), ()))

        return status

    async def handle_firmware_update(
        self, uav: MAVLinkUAV, target_id: str, blob: bytes
    ) -> AsyncIterator[Progress]:
        assert self.can_handle_firmware_update_target(target_id)

        # TODO(ntamas): validate .abin firmware

        # Upload firmware
        async with aclosing(MAVFTP.for_uav(uav)) as ftp:
            async with ftp.put_gen(blob, "/ardupilot.abin") as gen:
                async for progress in gen:
                    # Scale progress down to a max of 90% -- the remaining
                    # 10% will be rebooting and checking the result
                    percentage = progress.percentage
                    if percentage is not None:
                        yield progress.update(percentage=int(percentage * 0.9))

        # Progress is now at 90%. Ask for a reboot to the bootloader
        yield Progress(percentage=90)
        await uav.reboot_after_update()

        # Wait until the UAV becomes disconnected, but at least two seconds
        start = monotonic()
        while True:
            await sleep(0.5)

            dt = monotonic() - start
            if dt >= 2 and not uav.is_connected:
                break
            if dt >= 5:
                raise RuntimeError("UAV failed to reboot after uploading new firmware")

        # We have no way to know when the update is finished, so we pretend
        # that it is going to take about a minute. We wait for at most two
        # minutes and update the progress slowly.
        start = monotonic()
        while not uav.is_connected:
            await sleep(0.5)

            dt = monotonic() - start
            if dt > 120:
                # We waited for two minutes, so we give up
                raise RuntimeError("Firmware update timed out")
            elif dt >= 100:
                # Pretend a slower percentage update from 98% onwards
                yield Progress(percentage=99)
            elif dt >= 80:
                # Pretend a slower percentage update from 98% onwards
                yield Progress(percentage=98)
            else:
                # Pretend a percentage update of 1% every 10 seconds
                yield Progress(percentage=90 + int(dt) // 10)

        # Wait 2 more seconds to make sure that the initialization process
        # has finished on the drone
        yield Progress(percentage=99)
        await sleep(2)

        # Check whether the firmware update was successful
        async with aclosing(MAVFTP.for_uav(uav)) as ftp:
            async with ftp.ls("/") as gen:
                entries: list[str] = []
                async for entry in gen:
                    entries.append(entry.name.lower())

            if "ardupilot.abin" in entries:
                result = FirmwareUpdateResult.UNSUPPORTED
            elif "ardupilot-verify.abin" in entries:
                result = FirmwareUpdateResult.FAILED_TO_VERIFY
            elif "ardupilot-verify-failed.abin" in entries:
                result = FirmwareUpdateResult.INVALID
            elif "ardupilot-flash.abin" in entries:
                result = FirmwareUpdateResult.FLASHING_FAILED
            elif "ardupilot-flashed.abin" in entries:
                result = FirmwareUpdateResult.SUCCESS
            else:
                result = FirmwareUpdateResult.UNSUPPORTED

            if not result.successful:
                raise RuntimeError(result.describe())

            # Try to delete the firmware file now that it is not needed but do
            # not raise an error if it fails
            try:
                await ftp.rm("/ardupilot-flashed.abin")
            except Exception:
                uav.driver.log.warning(
                    "Failed to delete the firmware file after update",
                    extra={"id": log_id_for_uav(uav)},
                )

        yield Progress(percentage=100)

    def is_prearm_check_in_progress(
        self, heartbeat: MAVLinkMessage, sys_status: MAVLinkMessage
    ) -> bool:
        # Information not reported by ArduPilot by default
        return False

    def is_prearm_error_message(self, text: str) -> bool:
        return text.startswith("PreArm: ") or text.startswith("Arm: ")

    def is_rth_flight_mode(self, base_mode: int, custom_mode: int) -> bool:
        return bool(base_mode & 1) and (custom_mode == 6 or custom_mode == 21)

    def prepare_mavftp_parameter_upload(
        self, parameters: dict[str, float]
    ) -> tuple[str, bytes]:
        data = encode_parameters_to_packed_format(parameters)
        return "@PARAM/param.pck", data

    def process_prearm_error_message(self, text: str) -> str:
        return text[8:]

    def refine_with_capabilities(self, capabilities: int):
        result = super().refine_with_capabilities(capabilities)

        if isinstance(result, self.__class__) and not isinstance(
            result, ArduPilotWithSkybrush
        ):
            mask = ArduPilotWithSkybrush.CAPABILITY_MASK
            if (capabilities & mask) == mask:
                result = ArduPilotWithSkybrush(self)  # pyright: ignore[reportAbstractUsage]

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
    def supports_mavftp_parameter_upload(self) -> bool:
        return True

    @property
    def supports_repositioning(self) -> bool:
        # ArduCopter supports MAV_CMD_DO_REPOSITION since ArduCopter 4.1.0,
        # BUT it does not accept NaN in the altitude field. PX4 accepts NaN
        # and we rely on this to express our intention to use the current
        # altitude, so we cannot return True here until ArduCopter gains a
        # similar feature.
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


################################################################################
## ArduPilot packed parameter format handling
################################################################################


@dataclass
class PackedParameter:
    """A single entry in an ArduPilot-specific packed parameter representation."""

    name: bytes
    """Name of the parameter."""

    type: MAVParamType | None
    """Type of the parameter in the packed representation. Not necessarily the
    same as the type of the parameter in the onboard storage; ArduPilot will
    convert between the two if needed.
    """

    value: float
    """The value of the parameter."""

    default_value: float | None = None
    """The default value of the parameter, if known. Can be left empty if you
    want to _encode_ parameters into a packed representation instead of
    decoding them.
    """


_packed_param_header = Struct("<HHH")
_packed_type_to_mav_type: list[int] = [
    0,
    MAVParamType.INT8,
    MAVParamType.INT16,
    MAVParamType.INT32,
    MAVParamType.REAL32,
] + [0] * 11
_mav_type_to_packed_type: dict[MAVParamType, int] = {
    MAVParamType.INT8: 1,
    MAVParamType.INT16: 2,
    MAVParamType.INT32: 3,
    MAVParamType.REAL32: 4,
}
_packed_param_formats: list[Struct | None] = [
    None,
    Struct("<b"),
    Struct("<h"),
    Struct("<i"),
    Struct("<f"),
] + [None] * 11


def decode_parameters_from_packed_format(
    data: bytes | IO[bytes],
) -> Iterable[PackedParameter]:
    """Decodes an ArduPilot packed parameter bundle into an iterable of
    PackedParameter_ instances.

    The input must be a packed parameter bundle retrieved from the vehicle
    by downloading ``@PARAM/param.pck`` via MAVFTP. See the following file for
    more details:

    https://github.com/ArduPilot/ardupilot/blob/master/libraries/AP_Filesystem/README.md

    Args:
        data: the packed parameter bundle as a bytes object or as a readable
            stream

    Yields:
        PackedParameter_ instances decoded from the packed parameter bundle.
    """
    fp: IO[bytes] = BytesIO(data) if isinstance(data, bytes) else cast(IO[bytes], data)

    header_bytes = fp.read(_packed_param_header.size)

    magic: int
    num_params: int

    magic, num_params, _ = _packed_param_header.unpack(header_bytes)

    if magic != 0x671B and magic != 0x671C:
        raise RuntimeError("invalid magic bytes in packed param stream")

    has_defaults = magic == 0x671C

    prev_name = b""

    reader = partial(fp.read, 1)

    for _ in range(num_params):
        # Skip leading zeros
        for byte in iter(reader, b""):
            val = ord(byte)
            if val:
                type = val & 0x0F
                flags = (val & 0xF0) >> 4
                break
        else:
            # End of file, stop iteration
            break

        byte = reader()
        common_length, length = ord(byte) & 0x0F, (ord(byte) >> 4) + 1

        name = prev_name[:common_length] + fp.read(length)
        param_type: MAVParamType = MAVParamType(_packed_type_to_mav_type[type])
        struct = _packed_param_formats[type]
        value = struct.unpack(fp.read(struct.size)) if struct else None

        if has_defaults:
            if flags & 0x01:
                # Default value also provided
                default_value = struct.unpack(fp.read(struct.size)) if struct else None
            else:
                # Parameter is at its default value
                default_value = value
        else:
            default_value = None

        if value is not None:
            yield PackedParameter(
                name,
                param_type,
                value[0],
                default_value[0] if default_value is not None else None,
            )

        prev_name = name


def _propose_mav_type_for_value(value: float) -> MAVParamType:
    if value.is_integer():
        if -128 <= value <= 127:
            return MAVParamType.INT8
        elif -32768 <= value <= 32767:
            return MAVParamType.INT16
        elif -2147483648 <= value <= 2147483647:
            return MAVParamType.INT32
    return MAVParamType.REAL32


def encode_parameters_to_packed_format(
    parameters: Sequence[PackedParameter] | dict[str, float],
) -> bytes:
    """Encodes a sequence of PackedParameter_ instances into a packed parameter
    bundle that is suitable for uploading to `@PARAM/param.pck` via MAVFTP.

    Default values are ignored in the input. You may also provide a dict of
    name-value pairs.

    Note that the decoding and the encoding process is not symmetric. During
    encoding, the `total_length` field in the header is the total length of the
    bundle, in bytes. During decoding, the field contains the total number of
    parameters on the vehicle.

    Args:
        parameters: the PackedParameter_ instances or name-value pairs to encode

    Returns:
        The packed parameter bundle as a bytes object.
    """
    buf: list[bytes] = []

    # We don't know the total length yet so encode it as zero
    buf.append(_packed_param_header.pack(0x671B, len(parameters), 0))

    # Construct the parameter iterator
    if isinstance(parameters, dict):
        param_iter = (
            PackedParameter(name.upper().encode("ascii", "replace"), None, float(value))
            for name, value in parameters.items()
        )
    else:
        param_iter = iter(parameters)

    prev_name = b""

    for param in sorted(param_iter, key=lambda p: p.name.upper()):
        name = param.name
        if len(name) > 16:
            raise RuntimeError(f"Parameter name too long: {name!r}")

        mav_type = param.type or _propose_mav_type_for_value(param.value)
        packed_type = _mav_type_to_packed_type[mav_type]

        # Find length of longest common prefix of name and prev_name
        for i, (a, b) in enumerate(zip(name, prev_name, strict=False)):
            if a != b:
                common_len = i
                break
        else:
            # Since the iterator is sorted by name, this can happen only if
            # we have duplicate names or if prev_name is a prefix of name
            if name == prev_name:
                raise RuntimeError(f"Duplicate parameter name: {param.name!r}")
            else:
                common_len = len(prev_name)

        assert common_len < 16
        name_len = len(name) - common_len
        encoded_length = common_len | ((name_len - 1) << 4)

        buf.append(bytes([packed_type, encoded_length]))
        buf.append(name[common_len:])

        param_format = _packed_param_formats[packed_type]
        assert param_format is not None

        value = (
            int(param.value) if mav_type != MAVParamType.REAL32 else float(param.value)
        )
        buf.append(param_format.pack(value))

        prev_name = name

    total_length = sum(len(x) for x in buf)
    if total_length > 65535:
        raise RuntimeError(f"Packed parameter bundle too large: {total_length} bytes")

    # Now we can re-encode the header
    buf[0] = _packed_param_header.pack(0x671B, len(parameters), total_length)
    return b"".join(buf)
