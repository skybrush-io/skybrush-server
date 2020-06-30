"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from math import inf
from time import monotonic
from trio import move_on_after, sleep
from typing import Optional

from flockwave.gps.vectors import GPSCoordinate, VelocityNED

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.gps import GPSFix
from flockwave.server.model.uav import VersionInfo, UAVBase, UAVDriver
from flockwave.spec.errors import FlockwaveErrorCode

from .autopilots import Autopilot, UnknownAutopilot
from .enums import (
    GPSFixType,
    MAVCommand,
    MAVDataStream,
    MAVFrame,
    MAVMessageType,
    MAVModeFlag,
    MAVProtocolCapability,
    MAVResult,
    MAVState,
    MAVSysStatusSensor,
    PositionTargetTypemask,
)
from .types import MAVLinkMessage, spec
from .utils import mavlink_version_number_to_semver

__all__ = ("MAVLinkDriver",)


#: Conversion constant from seconds to microseconds
SEC_TO_USEC = 1000000

#: Magic number to force an arming or disarming operation even if it is unsafe
#: to do so
FORCE_MAGIC = 21196

#: "Not a number" constant, used in some MAVLink messages to indicate a default
#: value
nan = float("nan")


class MAVLinkDriver(UAVDriver):
    """Driver class for MAVLink-based drones.

    Attributes:
        app (SkybrushServer): the app in which the driver lives
        create_device_tree_mutator (callable): a function that should be
            called by the driver as a context manager whenever it wants to
            mutate the state of the device tree
        send_packet (callable): a function that should be called by the
            driver whenever it wants to send a packet. The function must
            be called with the packet to send, and a pair formed by the
            medium via which the packet should be forwarded and the
            destination address in that medium.
    """

    def __init__(self, app=None):
        """Constructor.

        Parameters:
            app: the app in which the driver lives
        """
        super().__init__()

        self.app = app

        self.create_device_tree_mutator = None
        self.log = None
        self.run_in_background = None
        self.send_packet = None

    def create_uav(self, id: str) -> "MAVLinkUAV":
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id: the identifier of the UAV to create

        Returns:
            MAVLinkUAV: an appropriate MAVLink UAV object
        """
        uav = MAVLinkUAV(id, driver=self)
        uav.notify_updated = partial(self.app.request_to_send_UAV_INF_message_for, [id])
        return uav

    def get_time_boot_ms() -> int:
        """Returns a monotonic "time since boot" timestamp in milliseconds that
        can be used in MAVLink messages.
        """
        return int(monotonic() * 1000)

    async def send_command_long(
        self,
        target: "MAVLinkUAV",
        command_id: int,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        param5: float = 0,
        param6: float = 0,
        param7: float = 0,
        *,
        confirmation: int = 0,
    ) -> bool:
        """Sends a MAVLink command to a given UAV and waits for an acknowlegment.

        Parameters:
            target: the UAV to send the command to
            param1: the first parameter of the command
            param2: the second parameter of the command
            param3: the third parameter of the command
            param4: the fourth parameter of the command
            param5: the fifth parameter of the command
            param6: the sixth parameter of the command
            param7: the seventh parameter of the command
            confirmation: confirmation count for commands that require a
                confirmation

        Returns:
            whether the command was executed successfully

        Raises:
            NotSupportedError: if the command is not supported by the UAV
        """
        response = await self.send_packet(
            (
                "COMMAND_LONG",
                {
                    "command": command_id,
                    "param1": param1,
                    "param2": param2,
                    "param3": param3,
                    "param4": param4,
                    "param5": param5,
                    "param6": param6,
                    "param7": param7,
                    "confirmation": confirmation,
                },
            ),
            target,
            wait_for_response=("COMMAND_ACK", {"command": command_id}),
        )
        result = response.result

        if result == MAVResult.UNSUPPORTED:
            raise NotSupportedError

        return result == MAVResult.ACCEPTED

    def _request_version_info_single(self, uav) -> VersionInfo:
        version_info = uav.get_last_message(MAVMessageType.AUTOPILOT_VERSION)
        result = {}

        for version in ("flight", "middleware", "os"):
            if getattr(version_info, f"{version}_sw_version", 0) > 0:
                result[f"{version}_sw"] = mavlink_version_number_to_semver(
                    getattr(version_info, f"{version}_sw_version", 0),
                    getattr(version_info, f"{version}_custom_version", None),
                )

        if version_info.board_version > 0:
            result["board"] = mavlink_version_number_to_semver(
                version_info.board_version
            )

        return result

    async def _send_fly_to_target_signal_single(self, uav, target) -> None:
        type_mask = (
            PositionTargetTypemask.VX_IGNORE
            | PositionTargetTypemask.VY_IGNORE
            | PositionTargetTypemask.VZ_IGNORE
            | PositionTargetTypemask.AX_IGNORE
            | PositionTargetTypemask.AY_IGNORE
            | PositionTargetTypemask.AZ_IGNORE
            | PositionTargetTypemask.YAW_IGNORE
            | PositionTargetTypemask.YAW_RATE_IGNORE
        )

        if target.amsl is None:
            frame = MAVFrame.GLOBAL_RELATIVE_ALT_INT
            if target.agl is None:
                # We cannot simply set Z_IGNORE in the type mask because that
                # does not work with ArduCopter (it would ignore the whole
                # position).
                altitude = uav.status.position.agl
            else:
                altitude = target.agl
        else:
            frame = MAVFrame.GLOBAL_RELATIVE_ALT_INT
            altitude = target.amsl

        lat, lon = int(target.lat * 1e7), int(target.lon * 1e7)

        message = spec.set_position_target_global_int(
            time_boot_ms=self.get_time_boot_ms(),
            coordinate_frame=frame,
            type_mask=type_mask,
            # position
            lat_int=lat,
            lon_int=lon,
            alt=altitude,
            # velocity
            vx=0,
            vy=0,
            vz=0,
            # acceleration or force
            afx=0,
            afy=0,
            afz=0,
            # yaw
            yaw=0,
            yaw_rate=0,
        )
        response = spec.position_target_global_int(
            # position
            lat_int=lat,
            lon_int=lon,
            # note that we don't check the altitude in the response because the
            # position target feedback could come in AMSL or AGL
        )
        await self.send_packet(message, uav, wait_for_response=response)

    async def _send_landing_signal_single(self, uav) -> None:
        success = await self.send_command_long(uav, MAVCommand.NAV_LAND)
        if not success:
            raise RuntimeError("Landing command failed")

    async def _send_motor_start_stop_signal_single(
        self, uav, start: bool, force: bool = False
    ) -> None:
        if not await self.send_command_long(
            uav,
            MAVCommand.COMPONENT_ARM_DISARM,
            1 if start else 0,
            FORCE_MAGIC if force else 0,
        ):
            raise RuntimeError(
                "Failed to arm motors" if start else "Failed to disarm motors"
            )

    async def _send_reset_signal_single(self, uav, component) -> None:
        if not component:
            # Resetting the whole UAV, this is supported
            success = await self.send_command_long(
                uav, MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN, 1  # reboot autopilot
            )
            if not success:
                raise RuntimeError("Reset command failed")
        else:
            # No component resets are implemented on this UAV yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_return_to_home_signal_single(self, uav) -> None:
        return await self.send_command_long(uav, MAVCommand.NAV_RETURN_TO_LAUNCH)

    async def _send_shutdown_signal_single(self, uav) -> None:
        await self._send_motor_start_stop_signal_single(uav, start=False, force=True)

        if not await self.send_command_long(
            uav, MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN, 2  # shutdown autopilot
        ):
            raise RuntimeError("Failed to send shutdown command to autopilot")

    async def _send_takeoff_signal_single(self, uav) -> None:
        await self._send_motor_start_stop_signal_single(uav, start=True)

        # Wait a bit to give the autopilot some time to start the motors, just
        # in case. Not sure whether this is needed.
        await sleep(0.1)

        if not await self.send_command_long(
            uav,
            MAVCommand.NAV_TAKEOFF,
            param4=nan,  # yaw should stay the same
            param7=5,  # takeoff to 5m
        ):
            raise RuntimeError("Failed to send takeoff command")


@dataclass
class MAVLinkMessageRecord:
    """Simple object holding a pair of a MAVLink message and the corresponding
    monotonic timestamp when the message was observed.
    """

    message: MAVLinkMessage = None
    timestamp: float = None

    @property
    def age(self) -> float:
        """Returns the number of seconds elapsed since the record was updated
        the last time.
        """
        return monotonic() - self.timestamp

    def update(self, message: MAVLinkMessage) -> None:
        """Updates the record with a new MAVLink message."""
        self.message = message
        self.timestamp = monotonic()


class MAVLinkUAV(UAVBase):
    """Subclass for UAVs created by the driver for MAVLink-based drones.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        self._autopilot = UnknownAutopilot
        self._battery = BatteryInfo()
        self._gps_fix = GPSFix()
        self._is_connected = False
        self._last_messages = defaultdict(MAVLinkMessageRecord)
        self._mavlink_version = 1
        self._network_id = None
        self._position = GPSCoordinate()
        self._velocity = VelocityNED()
        self._system_id = None

        self.notify_updated = None

    def assign_to_network_and_system_id(self, network_id: str, system_id: int) -> None:
        """Assigns the UAV to the MAVLink network with the given network ID.
        The UAV is assumed to have the given system ID in the given network, and
        it is assumed to have a component ID of 1 (primary autopilot). We are
        not talking to any other component of a MAVLink system yet.
        """
        if self._network_id is not None:
            raise RuntimeError(
                f"This UAV is already a member of MAVLink network {self._network_id}"
            )

        self._network_id = network_id
        self._system_id = system_id

    def handle_message_autopilot_version(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink AUTOPILOT_VERSION message targeted at
        this UAV.
        """
        self._store_message(message)

        if self._mavlink_version < 2 and (
            message.capabilities & MAVProtocolCapability.MAVLINK2
        ):
            # Autopilot supports MAVLink 2 so switch to it
            self._mavlink_version = 2

            # The other side has to know that we have switched; we do it by
            # sending it a REQUEST_AUTOPILOT_CAPABILITIES message again
            self.driver.run_in_background(self._request_autopilot_capabilities)

    def handle_message_heartbeat(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        if self._mavlink_version < 2 and message.get_msgbuf()[0] == 253:
            # Other side sent a MAVLink 2 heartbeat so we can switch to MAVLink
            # 2 as well
            self._mavlink_version = 2

        self._store_message(message)

        if not self._is_connected:
            self._autopilot = Autopilot.from_heartbeat(message)
            self.notify_reconnection()

        self._update_errors_from_sys_status_and_heartbeat()

        self.update_status(
            mode=self._autopilot.describe_mode(message.base_mode, message.custom_mode)
        )

    def handle_message_global_position_int(self, message: MAVLinkMessage):
        # TODO(ntamas): reboot detection with time_boot_ms

        if abs(message.lat) <= 900000000:
            self._position.lat = message.lat / 1e7
            self._position.lon = message.lon / 1e7
            self._position.amsl = message.alt / 1e3
            self._position.agl = message.relative_alt / 1e3
        else:
            # Some drones, such as the Parrot Bebop 2, use 2^31-1 as latitude
            # and longitude to indicate that no GPS fix has been obtained yet,
            # so treat any values outside the valid latitude range as invalid
            self._position.lat = (
                self._position.lon
            ) = self._position.amsl = self._position.agl = 0

        self._velocity.x = message.vx / 100
        self._velocity.y = message.vy / 100
        self._velocity.z = message.vz / 100

        if abs(message.hdg) <= 3600:
            heading = message.hdg / 10
        else:
            heading = 0

        self.update_status(
            position=self._position, velocity=self._velocity, heading=heading
        )
        self.notify_updated()

    def handle_message_gps_raw_int(self, message: MAVLinkMessage):
        num_sats = message.satellites_visible
        self._gps_fix.type = GPSFixType(message.fix_type).to_ours()
        self._gps_fix.num_satellites = (
            num_sats if num_sats < 255 else None
        )  # 255 = unknown
        self.update_status(gps=self._gps_fix)
        self.notify_updated()

    def handle_message_sys_status(self, message: MAVLinkMessage):
        self._store_message(message)
        self._update_errors_from_sys_status_and_heartbeat()

        # Update battery status
        self._battery.voltage = message.voltage_battery / 1000
        self._battery.percentage = message.battery_remaining
        self.update_status(battery=self._battery)

        self.notify_updated()

    def handle_message_system_time(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        previous_message = self._get_previous_copy_of_message(message)
        if previous_message:
            # TODO(ntamas): compare the time since boot with the previous
            # version to detect reboot events
            pass

        self._store_message(message)

    @property
    def is_connected(self) -> bool:
        """Returns whether the UAV is connected to the network."""
        return self._is_connected

    @property
    def mavlink_version(self) -> int:
        """The MAVLink version supported by this UAV."""
        return self._mavlink_version

    @property
    def network_id(self) -> str:
        """The network ID of the UAV."""
        return self._network_id

    @property
    def system_id(self) -> str:
        """The system ID of the UAV."""
        return self._system_id

    def notify_disconnection(self) -> None:
        """Notifies the UAV state object that we have detected that it has been
        disconnected from the network.
        """
        self._is_connected = False
        # TODO(ntamas): trigger a warning flag in the UAV?

    def notify_reconnection(self) -> None:
        """Notifies the UAV state object that it has been reconnected to the
        network.
        """
        self._is_connected = True
        # TODO(ntamas): clear a warning flag in the UAV?

        if self._was_probably_rebooted_after_reconnection():
            self._handle_reboot()

    async def _configure_data_streams(self) -> None:
        """Configures the data streams that we want to receive from the UAV."""
        # We give ourselves 5 seconds to configure everything
        with move_on_after(5):
            # TODO(ntamas): this is unsafe; there are no confirmations for
            # REQUEST_DAYA_STREAM commands so we never know if we succeeded or
            # not
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=0, req_message_rate=0, start_stop=0
                ),
                target=self,
            )

            # EXTENDED_STATUS: we need SYS_STATUS from it for the general status
            # flags and GPS_RAW_INT for the GPS fix info.
            # We might also need MISSION_CURRENT.
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=MAVDataStream.EXTENDED_STATUS,
                    req_message_rate=1,
                    start_stop=1,
                ),
                target=self,
            )

            # POSITION: we need GLOBAL_POSITION_INT for position and velocity
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=MAVDataStream.POSITION,
                    req_message_rate=2,
                    start_stop=1,
                ),
                target=self,
            )

    def _handle_reboot(self) -> None:
        """Handles a reboot event on the autopilot and attempts to re-initialize
        the data streams.
        """
        # Revert to MAVLink version 1 in case the UAV was somehow reset and it
        # does not "understand" MAVLink v2 in its new configuration
        self._mavlink_version = 1

        self.driver.run_in_background(self._configure_data_streams)
        self.driver.run_in_background(self._request_autopilot_capabilities)

    def get_age_of_message(self, type: int, now: Optional[float] = None) -> float:
        """Returns the number of seconds elapsed since we have last seen a
        message of the given type.
        """
        record = self._last_messages.get(int(type))
        if now is None:
            now = monotonic()
        return record.timestamp - now if record else inf

    def get_last_message(self, type: int) -> Optional[MAVLinkMessage]:
        """Returns the last MAVLink message that was observed with the given
        type or `None` if we have not observed such a message yet.
        """
        record = self._last_messages.get(int(type))
        return record.message if record else None

    async def _request_autopilot_capabilities(self) -> None:
        """Retrieves the capabilities of the autopilot via MAVLink."""
        await self.driver.send_command_long(
            self, MAVCommand.REQUEST_AUTOPILOT_CAPABILITIES, param1=1
        )

    def _get_previous_copy_of_message(
        self, message: MAVLinkMessage
    ) -> Optional[MAVLinkMessage]:
        """Returns the previous copy of this MAVLink message, or `None` if we
        have not observed such a message yet.
        """
        record = self._get_previous_record_of_message(message)
        return record.message if record else None

    def _get_previous_record_of_message(
        self, message: MAVLinkMessage
    ) -> Optional[MAVLinkMessageRecord]:
        """Returns the previous copy of this MAVLink message and its timestamp,
        or `None` if we have not observed such a message yet.
        """
        return self._last_messages.get(message.get_msgId())

    def _store_message(self, message: MAVLinkMessage) -> None:
        """Stores the given MAVLink message in the dictionary that maps
        MAVLink message types to their most recent versions that were seen
        for this UAV.
        """
        self._last_messages[message.get_msgId()].update(message)

    def _update_errors_from_sys_status_and_heartbeat(self):
        """Updates the error codes based on the most recent HEARTBEAT and
        SYS_STATUS messages. We need both to have an accurate picture of what is
        going on, hence a separate function that is called from both message
        handlers.
        """
        heartbeat = self.get_last_message(MAVMessageType.HEARTBEAT)
        sys_status = self.get_last_message(MAVMessageType.SYS_STATUS)
        if not heartbeat or not sys_status:
            return

        # Check error conditions from SYS_STATUS
        sensor_mask = (
            sys_status.onboard_control_sensors_enabled
            & sys_status.onboard_control_sensors_present
        )
        not_healthy_sensors = sensor_mask & (
            # Python has no proper bitwise negation on unsigned integers
            # so we use XOR instead
            sys_status.onboard_control_sensors_health
            ^ 0xFFFFFFFF
        )

        has_gyro_error = not_healthy_sensors & (
            MAVSysStatusSensor.GYRO_3D | MAVSysStatusSensor.GYRO2_3D
        )
        has_mag_error = not_healthy_sensors & (
            MAVSysStatusSensor.MAG_3D | MAVSysStatusSensor.MAG2_3D
        )
        has_accel_error = not_healthy_sensors & (
            MAVSysStatusSensor.ACCEL_3D | MAVSysStatusSensor.ACCEL2_3D
        )
        has_baro_error = not_healthy_sensors & (
            MAVSysStatusSensor.ABSOLUTE_PRESSURE
            | MAVSysStatusSensor.DIFFERENTIAL_PRESSURE
        )
        has_gps_error = not_healthy_sensors & MAVSysStatusSensor.GPS
        has_motor_error = not_healthy_sensors & (
            MAVSysStatusSensor.MOTOR_OUTPUTS | MAVSysStatusSensor.REVERSE_MOTOR
        )
        has_geofence_error = not_healthy_sensors & MAVSysStatusSensor.GEOFENCE
        has_rc_error = not_healthy_sensors & MAVSysStatusSensor.RC_RECEIVER
        has_battery_error = not_healthy_sensors & MAVSysStatusSensor.BATTERY
        has_logging_error = not_healthy_sensors & MAVSysStatusSensor.LOGGING

        errors = {
            FlockwaveErrorCode.AUTOPILOT_INIT_FAILED: heartbeat.system_status
            == MAVState.UNINIT,
            FlockwaveErrorCode.AUTOPILOT_INITIALIZING: heartbeat.system_status
            == MAVState.BOOT,
            FlockwaveErrorCode.UNSPECIFIED_ERROR: heartbeat.system_status
            == MAVState.CRITICAL
            and not not_healthy_sensors,
            FlockwaveErrorCode.UNSPECIFIED_CRITICAL_ERROR: heartbeat.system_status
            == MAVState.EMERGENCY
            and not not_healthy_sensors,
            FlockwaveErrorCode.MAGNETIC_ERROR: has_mag_error,
            FlockwaveErrorCode.GYROSCOPE_ERROR: has_gyro_error,
            FlockwaveErrorCode.ACCELEROMETER_ERROR: has_accel_error,
            FlockwaveErrorCode.PRESSURE_SENSOR_ERROR: has_baro_error,
            FlockwaveErrorCode.GPS_SIGNAL_LOST: has_gps_error,
            FlockwaveErrorCode.MOTOR_MALFUNCTION: has_motor_error,
            FlockwaveErrorCode.GEOFENCE_VIOLATION: has_geofence_error,
            FlockwaveErrorCode.RC_SIGNAL_LOST_WARNING: has_rc_error,
            FlockwaveErrorCode.BATTERY_CRITICAL: has_battery_error,
            FlockwaveErrorCode.LOGGING_DEACTIVATED: has_logging_error,
            # valid in our patched ArduCopter only, the stock ArduCopter
            # does not use this flag
            FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS: heartbeat.system_status
            == MAVState.CALIBRATING,
            # If the motors are running but we are not in the air yet; we use an
            # informational flag to let the user know
            FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND: (
                heartbeat.base_mode & MAVModeFlag.SAFETY_ARMED
                and heartbeat.system_status == MAVState.STANDBY
            ),
        }

        # Update the error flags as needed
        self.ensure_errors(errors)

    def _was_probably_rebooted_after_reconnection(self) -> bool:
        """Returns whether the UAV was probably rebooted recently, _assuming_
        that a reconnection event happened.

        This function _must_ be called only after a reconnection event. Right
        now we always return `True`, but we could implement a more sophisticated
        check in the future based on the `SYSTEM_TIME` messages and whether the
        `time_boot_ms` timestamp has decreased.
        """
        return True
