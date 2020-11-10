"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from math import inf
from time import monotonic
from trio import fail_after, move_on_after, sleep, TooSlowError
from typing import List, Optional, Union

from flockwave.gps.time import datetime_to_gps_time_of_week, gps_time_of_week_to_utc
from flockwave.gps.vectors import GPSCoordinate, VelocityNED

from flockwave.server.concurrency import aclosing
from flockwave.server.errors import NotSupportedError
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.geofence import GeofenceConfigurationRequest, GeofenceStatus
from flockwave.server.model.gps import GPSFix
from flockwave.server.model.commands import (
    create_parameter_command_handler,
    create_version_command_handler,
)
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.uav import VersionInfo, UAVBase, UAVDriver
from flockwave.server.utils import to_uppercase_string
from flockwave.spec.errors import FlockwaveErrorCode

from skybrush import (
    get_coordinate_system_from_show_specification,
    get_geofence_configuration_from_show_specification,
    get_light_program_from_show_specification,
    get_trajectory_from_show_specification,
)
from skybrush.formats import SkybrushBinaryShowFile

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
from .ftp import MAVFTP
from .packets import DroneShowStatus
from .types import MAVLinkMessage, spec
from .utils import log_id_for_uav, mavlink_version_number_to_semver

__all__ = ("MAVLinkDriver",)


#: Conversion constant from seconds to microseconds
SEC_TO_USEC = 1000000

#: Magic number to force an arming or disarming operation even if it is unsafe
#: to do so
FORCE_MAGIC = 21196

#: Helper constant used when we try to send an empty byte array via MAVLink
_EMPTY = b"\x00" * 256

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
        self.mandatory_custom_mode = None
        self.run_in_background = None
        self.send_packet = None

        self._default_timeout = 2
        self._default_retries = 5

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

    handle_command_param = create_parameter_command_handler(
        name_validator=to_uppercase_string
    )
    handle_command_version = create_version_command_handler()

    async def handle_command_mode(self, uav: "MAVLinkUAV", mode: Optional[str] = None):
        """Returns or sets the (custom) flight mode of the UAV.

        Parameters:
            mode: the name of the mode to set
        """
        if mode is None:
            return getattr(uav.status, "mode", "unknown mode")
        else:
            await uav.set_mode(mode)
            return f"Mode changed to {mode!r}"

    async def handle_command_show(
        self, uav: "MAVLinkUAV", command: Optional[str] = None
    ):
        """Allows the user to remove the current show file.

        Parameters:
            command: must be 'remove' to remove the current show
        """
        if command is None:
            raise RuntimeError(
                "Missing subcommand; add 'remove' to remove the current show."
            )
        elif command == "remove":
            await uav.remove_show()
            return "Show removed."
        else:
            raise RuntimeError(f"Unknown subcommand: {command!r}")

    async def handle_command___show_upload(self, uav: "MAVLinkUAV", *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        try:
            await uav.upload_show(show)
        except TooSlowError as ex:
            self.log.error(str(ex))
            raise
        except Exception as ex:
            self.log.error(str(ex))
            raise

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
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
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
            timeout: command timeout in seconds; `None` means to use the default
                timeout for the driver. Retries will be attempted if no response
                arrives to the command within the given time interval
            retries: maximum number of retries for the command (not counting the
                initial attempt); `None` means to use the default retry count
                for the driver.

        Returns:
            whether the command was executed successfully

        Raises:
            TooSlowError: if the UAV failed to respond in time, even after
                re-sending the command as needed
            NotSupportedError: if the command is not supported by the UAV
        """
        # TODO(ntamas): use confirmation when attempt > 1

        if timeout is None or timeout <= 0:
            timeout = self._default_timeout
        if retries is None or retries < 0:
            retries = self._default_retries

        confirmation = 0
        result = None

        while retries >= 0:
            try:
                with fail_after(timeout):
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
                    break
            except TooSlowError:
                retries -= 1
                confirmation = 1

        if result is None:
            raise TooSlowError(f"no response received for command {command_id} in time")

        if result == MAVResult.UNSUPPORTED:
            raise NotSupportedError

        return result == MAVResult.ACCEPTED

    async def send_packet_with_retries(
        self,
        spec,
        target,
        *,
        wait_for_response=None,
        wait_for_one_of=None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> MAVLinkMessage:
        """Sends a packet to the given target UAV, waiting for a matching reply
        packet and re-sending the packet a given number of times when no
        response arrives in time.

        Parameters:
            spec: the specification of the MAVLink message to send
            target: the UAV to send the message to
            wait_for_response: when not `None`, specifies a MAVLink message to
                wait for as a response. The message specification will be
                matched with all incoming MAVLink messages that have the same
                type as the type in the specification; all parameters of the
                incoming message must be equal to the template specified in
                this argument to accept it as a response. The source system of
                the MAVLink message must also be equal to the system ID of the
                UAV where this message was sent.
            timeout: timeout in seconds; `None` means to use the default
                timeout for the driver. Retries will be attempted if no response
                arrives to the packet within the given time interval
            retries: maximum number of retries for the packet (not counting the
                initial attempt); `None` means to use the default retry count
                for the driver.
        """
        if timeout is None or timeout <= 0:
            timeout = self._default_timeout
        if retries is None or retries < 0:
            retries = self._default_retries

        if not wait_for_response and not wait_for_one_of:
            raise RuntimeError(
                "'wait_for_response' and 'wait_for_one_of' must be provided"
            )

        response = None

        while retries >= 0:
            try:
                with fail_after(timeout):
                    response = await self.send_packet(
                        spec,
                        target,
                        wait_for_response=wait_for_response,
                        wait_for_one_of=wait_for_one_of,
                    )
                    break
            except TooSlowError:
                retries -= 1

        if response is None:
            raise TooSlowError("no response received for the outbound packet in time")

        return response

    def _request_preflight_report_single(self, uav) -> PreflightCheckInfo:
        return uav.preflight_status

    def _request_version_info_single(self, uav) -> VersionInfo:
        return uav.get_version_info()

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
        await self.send_packet_with_retries(message, uav, wait_for_response=response)

    async def _send_landing_signal_single(self, uav) -> None:
        success = await self.send_command_long(uav, MAVCommand.NAV_LAND)
        if not success:
            raise RuntimeError("Landing command failed")

    async def _send_light_or_sound_emission_signal_single(
        self, uav, signals: List[str], duration: int
    ) -> None:
        if "light" in signals:
            # The Skybrush firmware uses a "secret" LED instance / pattern
            # combination (42/42) to make the LED emit a flash pattern
            message = spec.led_control(
                instance=42, pattern=42, custom_len=0, custom_bytes=_EMPTY
            )
            await self.send_packet(message, uav)

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

    async def _send_takeoff_signal_single(
        self, uav, *, scheduled: bool = False
    ) -> None:
        if scheduled:
            # Ignore this; scheduled takeoffs are managed by the ScheduledTakeoffManager
            return

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
    """Subclass for UAVs created by the driver for MAVLink-based drones."""

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        #: Model of the autopilot used by this drone
        self._autopilot = UnknownAutopilot()

        #: Battery status of the drone
        self._battery = BatteryInfo()

        #: Stores whether we are connecting to the drone for the first time;
        #: used to prevent a "probably rebooted warning" for the first connection
        self._first_connection = True

        #: Current GPS fix status of the drone
        self._gps_fix = GPSFix()

        #: Stores whether we are currently connected to the drone (i.e. received
        #: a heartbeat recently)
        self._is_connected = False

        #: Stores whether the drone is authorized to perform a scheduled takeoff
        self._is_scheduled_takeoff_authorized = False

        #: Stores a mapping of each MAVLink message type received from the drone
        #: to the most recent copy of the message of that type. Some of these
        #: records may be cleared when we don't detect a heartbeat from the
        #: drone any more
        self._last_messages = defaultdict(MAVLinkMessageRecord)

        #: Stores the last "time since boot" timestamp received from the drone
        #: in any of the messages where we observe this timestamp
        self._last_time_since_boot_msec = None

        #: Stores the MAVLink network ID of the drone (not part of the MAVLink
        #: messages; used by us to track which MAVLink network of ours the
        #: drone belongs to)
        self._network_id = None

        #: Status of the preflight checks on the drone
        self._preflight_status = PreflightCheckInfo()

        #: Current global position of the drone
        self._position = GPSCoordinate()

        #: Scheduled takeoff time of the drone, as a UNIX timestamp, in seconds
        self._scheduled_takeoff_time = None

        #: Scheduled takeoff time of the drone, as a GPS time-of-week timestamp,
        #: in seconds
        self._scheduled_takeoff_time_gps_time_of_week = None

        #: MAVLink system ID of the drone
        self._system_id = None

        #: Current velocity of the drone in NED coordinate system, m/sec
        self._velocity = VelocityNED()

        self.notify_updated = None

        self._reset_mavlink_version()

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

    async def clear_scheduled_takeoff_time(self) -> None:
        """Clears the scheduled takeoff time of the UAV."""
        await self.set_scheduled_takeoff_time(None)

    async def configure_geofence(
        self, configuration: GeofenceConfigurationRequest
    ) -> None:
        """Configures the geofence on the UAV."""
        return await self._autopilot.configure_geofence(self, configuration)

    def get_age_of_message(self, type: int, now: Optional[float] = None) -> float:
        """Returns the number of seconds elapsed since we have last seen a
        message of the given type.
        """
        record = self._last_messages.get(int(type))
        if now is None:
            now = monotonic()
        return now - record.timestamp if record else inf

    async def get_geofence_status(self) -> GeofenceStatus:
        """Returns the status of the geofence of the UAV."""
        return await self._autopilot.get_geofence_status(self)

    def get_last_message(self, type: int) -> Optional[MAVLinkMessage]:
        """Returns the last MAVLink message that was observed with the given
        type or `None` if we have not observed such a message yet.
        """
        record = self._last_messages.get(int(type))
        return record.message if record else None

    async def get_parameter(self, name: str, fetch: bool = False) -> float:
        """Returns the value of a parameter from the UAV.

        Due to the nature of the MAVLink protocol, we will not be able to
        detect if a parameter does not exist as there will be no reply from
        the drone -- which is indistinguishable from a lost packet.
        """
        response = await self._get_parameter(name)
        return self._autopilot.decode_param_from_wire_representation(
            response.param_value, response.param_type
        )

    async def _get_parameter(self, name: str) -> MAVLinkMessage:
        """Retrieves the value of a parameter from the UAV and returns a
        MAVLink message encapsulating the name, index, value and type of
        the parameter.
        """
        param_id = name.encode("utf-8")[:16]
        return await self.driver.send_packet_with_retries(
            spec.param_request_read(param_id=param_id, param_index=-1),
            target=self,
            wait_for_response=spec.param_value(param_id=param_id),
            timeout=0.7,
        )

    def get_version_info(self) -> VersionInfo:
        """Returns a dictionary mapping component names of this UAV to the
        corresponding version numbers.
        """
        version_info = self.get_last_message(MAVMessageType.AUTOPILOT_VERSION)
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

    @property
    def is_scheduled_takeoff_authorized(self) -> bool:
        """Returns whether the scheduled takeoff of the UAV is authorized
        according to the status packets we received from the UAV.
        """
        return self._is_scheduled_takeoff_authorized

    @property
    def scheduled_takeoff_time(self) -> Optional[int]:
        """Returns the scheduled takeoff time of the UAV as a UNIX timestamp
        in seconds, truncated to an integer, or `None` if the UAV is not
        scheduled for an automatic takeoff.
        """
        return self._scheduled_takeoff_time

    @property
    def scheduled_takeoff_time_gps_time_of_week(self) -> Optional[int]:
        """Returns the scheduled takeoff time of the UAV as a GPS time of week
        value, or `None` if the UAV is not scheduled for an automatic takeoff.
        """
        return self._scheduled_takeoff_time_gps_time_of_week

    async def set_parameter(self, name: str, value: float) -> None:
        """Sets the value of a parameter on the UAV."""
        # We need to retrieve the current value of the parameter first because
        # we need its type
        param_id = name.encode("utf-8")[:16]
        response = await self._get_parameter(name)
        param_type = response.param_type
        encoded_value = self._autopilot.encode_param_to_wire_representation(
            value, param_type
        )
        await self.driver.send_packet_with_retries(
            spec.param_set(
                param_id=param_id,
                param_value=encoded_value,
                param_type=param_type,
            ),
            target=self,
            wait_for_response=spec.param_value(param_id=param_id),
            timeout=0.7,
        )

    def handle_message_autopilot_version(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink AUTOPILOT_VERSION message targeted at
        this UAV.
        """
        self._autopilot = self._autopilot.refine_with_capabilities(message.capabilities)

        self._store_message(message)

        if self._mavlink_version < 2:
            if message.capabilities & MAVProtocolCapability.MAVLINK2:
                # Autopilot supports MAVLink 2 so switch to it
                self._mavlink_version = 2

                # The other side has to know that we have switched; we do it by
                # sending it a REQUEST_AUTOPILOT_CAPABILITIES message again
                self.driver.run_in_background(self._request_autopilot_capabilities)
            else:
                # MAVLink 2 not supported by the drone. We do not support MAVLink 1
                # as most of the messages we use are MAVLink 2 only, so indicate
                # a protocol error at this point and bail out.
                self.ensure_error(FlockwaveErrorCode.AUTOPILOT_PROTOCOL_ERROR)

    def handle_message_drone_show_status(self, message: MAVLinkMessage):
        """Handles an incoming drone show specific status message targeted at
        this UAV.
        """
        data = DroneShowStatus.from_mavlink_message(message)

        self._gps_fix.num_satellites = data.num_satellites
        self._gps_fix.type = data.gps_fix

        gps_start_time = data.start_time if data.start_time >= 0 else None
        if gps_start_time != self._scheduled_takeoff_time_gps_time_of_week:
            self._scheduled_takeoff_time_gps_time_of_week = gps_start_time
            if gps_start_time is None:
                self._scheduled_takeoff_time = None
            else:
                self._scheduled_takeoff_time = int(
                    gps_time_of_week_to_utc(gps_start_time).timestamp()
                )

        self._is_scheduled_takeoff_authorized = data.has_takeoff_authorization

        debug = data.message.encode("utf-8")

        self.update_status(light=data.light, gps=self._gps_fix, debug=debug)
        self.notify_updated()

    def handle_message_heartbeat(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        if self._mavlink_version < 2 and message.get_msgbuf()[0] == 253:
            # Other side sent a MAVLink 2 heartbeat so we can switch to MAVLink
            # 2 as well
            self._mavlink_version = 2

        # Store a copy of the heartbeat
        self._store_message(message)

        if not self._is_connected:
            # We assume that the autopilot type stays the same even if we lost
            # contact with the drone or if it was rebooted
            if isinstance(self._autopilot, UnknownAutopilot):
                autopilot_cls = Autopilot.from_heartbeat(message)
                self._autopilot = autopilot_cls()

            self.notify_reconnection()

        # Do we already have basic information about the autopilot capabilities?
        # If we don't, ask for them.
        if not self.get_last_message(MAVMessageType.AUTOPILOT_VERSION):
            self.driver.run_in_background(self._request_autopilot_capabilities)

        # Update error codes and basic status info
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

        if abs(message.hdg) <= 36000:
            heading = message.hdg / 100
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
        if message.voltage_battery < 65535:
            self._battery.voltage = message.voltage_battery / 1000
        else:
            self._battery.voltage = 0.0
        if message.battery_remaining == -1:
            self._battery.percentage = None
        elif self._autopilot.is_battery_percentage_reliable:
            self._battery.percentage = message.battery_remaining
        else:
            self._battery.percentage = None
        self.update_status(battery=self._battery)

        self.notify_updated()

    def handle_message_system_time(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink SYSTEM_TIME message targeted at this UAV."""
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

        # Revert to the lowest MAVLink version that we support in case the UAV
        # was somehow reset and it does not "understand" MAVLink v2 in its new
        # configuration
        self._reset_mavlink_version()

    def _reset_mavlink_version(self) -> None:
        """Resets the MAVLink protocol version used by messages sent to this
        UAV to the default value.

        Currently we assume that all the drones we are trying to talk to
        support MAVLink 2, so we always reset to MAVLink 2.
        """
        self._mavlink_version = 2

    def notify_prearm_failure(self, message: str) -> None:
        """Notifies the UAV state object that a prearm check has failed."""
        self._preflight_status.message = message
        self._preflight_status.result = PreflightCheckResult.FAILURE

    def notify_reconnection(self) -> None:
        """Notifies the UAV state object that it has been reconnected to the
        network.
        """
        self._is_connected = True
        # TODO(ntamas): clear a warning flag in the UAV?

        if self._was_probably_rebooted_after_reconnection():
            if not self._first_connection:
                self.driver.log.warn(
                    f"UAV {self.id} might have been rebooted; reconfiguring"
                )

            self._first_connection = False
            self._handle_reboot()

    @property
    def preflight_status(self) -> PreflightCheckInfo:
        return self._preflight_status

    async def reload_show(self) -> None:
        """Asks the UAV to reload the current drone show file."""
        # param1 = 0 if we want to reload the show file
        await self.driver.send_command_long(self, MAVCommand.USER_1, 0)

    async def remove_show(self) -> None:
        """Asks the UAV to remove the current drone show file."""
        # param1 = 1 if we want to clear the show file
        await self.driver.send_command_long(self, MAVCommand.USER_1, 1)

    async def set_mode(self, mode: Union[int, str]) -> None:
        """Attempts to set the UAV in the given custom mode."""
        if isinstance(mode, str):
            try:
                mode = int(mode)
            except ValueError:
                pass

        if isinstance(mode, int):
            base_mode, submode = MAVModeFlag.CUSTOM_MODE_ENABLED, 0

        if isinstance(mode, str):
            try:
                base_mode, mode, submode = self._autopilot.get_flight_mode_numbers(mode)
            except NotSupportedError:
                raise ValueError("setting flight modes by name is not supported")

        await self.driver.send_command_long(
            self,
            MAVCommand.DO_SET_MODE,
            param1=float(base_mode),
            param2=float(mode),
            param3=float(submode),
        )

    @property
    def supports_scheduled_takeoff(self) -> bool:
        """Returns whether the UAV supports scheduled takeoffs."""
        return self._autopilot and self._autopilot.supports_scheduled_takeoff

    async def set_authorization_to_takeoff(self, value: bool = True) -> None:
        """Sets or clears whether the UAV has authorization to perform an
        automatic takeoff.
        """
        await self.set_parameter("SHOW_START_AUTH", 1 if value else 0)

    async def set_scheduled_takeoff_time(self, seconds: Optional[int]) -> None:
        """Sets the scheduled takeoff time of the UAV to the given timestamp in
        seconds. Only integer seconds are supported. Setting the takeoff time
        to `None` or a negative number will clear the takeoff time.
        """
        # The UAV needs GPS time of week so we convert it first. Note that we
        # convert the UNIX timestamp to a datetime first because UNIX timestamps
        # do not have leap seconds (every day is 86400 seconds in UNIX time) so
        # they are inherently ambiguous

        if seconds is None or seconds < 0:
            gps_time_of_week = -1
        else:
            dt = datetime.fromtimestamp(int(seconds), tz=timezone.utc)
            _, gps_time_of_week = datetime_to_gps_time_of_week(dt)

        await self.set_parameter("SHOW_START_TIME", gps_time_of_week)

    async def upload_show(self, show) -> None:
        coordinate_system = get_coordinate_system_from_show_specification(show)
        if coordinate_system.type != "nwu":
            raise RuntimeError("Only NWU coordinate systems are supported")

        light_program = get_light_program_from_show_specification(show)
        trajectory = get_trajectory_from_show_specification(show)
        geofence = get_geofence_configuration_from_show_specification(show)

        async with SkybrushBinaryShowFile.create_in_memory() as show_file:
            await show_file.add_trajectory(trajectory)
            await show_file.add_light_program(light_program)
            data = show_file.get_contents()

        # Upload show file
        async with aclosing(MAVFTP.for_uav(self)) as ftp:
            await ftp.put(data, "/collmot/show.skyb")

        # Ask drone to reload show file
        await self.reload_show()

        # Configure show origin and orientation
        # TODO(ntamas): this is not entirely accurate due to the back-and-forth
        # conversion happening between floats and ints; sometimes the 7th
        # decimal digit is off by one.
        await self.set_parameter(
            "SHOW_ORIGIN_LAT", int(coordinate_system.origin.lat * 1e7)
        )
        await self.set_parameter(
            "SHOW_ORIGIN_LNG", int(coordinate_system.origin.lon * 1e7)
        )
        await self.set_parameter("SHOW_ORIENTATION", coordinate_system.orientation)

        # Configure and enable geofence
        await self.configure_geofence(geofence)

    async def _configure_data_streams(self) -> None:
        """Configures the data streams that we want to receive from the UAV."""
        success = False

        # We give ourselves 60 seconds to configure everything. Most of the
        # internal functions time out on their own anyway
        with move_on_after(60):
            try:
                await self._configure_data_streams_with_fine_grained_commands()
                success = True
            except NotSupportedError:
                await self._configure_data_streams_with_legacy_commands()
                success = True
            except TooSlowError:
                # attempt timed out, even after retries, so we just give up
                pass

        # TODO(ntamas): keep on trying to configure stuff in the background if
        # we fail
        if not success:
            self.driver.log.warn(
                "Failed to configure data stream rates",
                extra={"id": log_id_for_uav(self)},
            )

    async def _configure_data_streams_with_fine_grained_commands(self) -> None:
        """Configures the intervals of the messages that we want to receive from
        the UAV using the newer `SET_MESSAGE_INTERVAL` MAVLink command.
        """
        stream_rates = [
            (MAVMessageType.SYS_STATUS, 1),
            (MAVMessageType.GPS_RAW_INT, 1),
            (MAVMessageType.GLOBAL_POSITION_INT, 2),
        ]

        for message_id, interval_hz in stream_rates:
            await self.driver.send_command_long(
                self,
                MAVCommand.SET_MESSAGE_INTERVAL,
                param1=message_id,
                param2=1000000 / interval_hz,
            )

    async def _configure_data_streams_with_legacy_commands(self) -> None:
        """Configures the data streams that we want to receive from the UAV
        using the deprecated `REQUEST_DATA_STREAM` MAVLink command.
        """
        # TODO(ntamas): this is unsafe; there are no confirmations for
        # REQUEST_DATA_STREAM commands so we never know if we succeeded or
        # not
        await self.driver.send_packet(
            spec.request_data_stream(req_stream_id=0, req_message_rate=0, start_stop=0),
            target=self,
        )

        # EXTENDED_STATUS: we need SYS_STATUS from it for the general status
        # flags and GPS_RAW_INT for the GPS fix info.
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

    async def _configure_mandatory_custom_mode(self) -> None:
        """Sets the drone to its mandatory custom mode after connection; used
        only for local experimentation with the SITL simulator where it is
        convenient to set up a custom mode in advance without an RC.
        """
        await sleep(2)
        try:
            await self.set_mode(self.driver.mandatory_custom_mode)
        except TooSlowError:
            self.driver.log.warn(
                "Failed to configure custom mode; no response in time",
                extra={"id": log_id_for_uav(self)},
            )
        except Exception:
            self.driver.log.exception(
                "Failed to configure custom mode", extra={"id": log_id_for_uav(self)}
            )

    def _handle_reboot(self) -> None:
        """Handles a reboot event on the autopilot and attempts to re-initialize
        the data streams.
        """
        self.driver.run_in_background(self._configure_data_streams)

        # No need to request the autopilot capabilities here; we do it after
        # every heartbeat if we don't have them yet. See the comment in
        # `self._request_autopilot_capabilities()` for an explanation.

        if self.driver.mandatory_custom_mode is not None:
            # Don't set the mode immediately because the drone might now
            # respond right after bootup
            self.driver.run_in_background(self._configure_mandatory_custom_mode)

    async def _request_autopilot_capabilities(self) -> None:
        """Sends a request to the autopilot to send its capabilities via MAVLink
        in a separate packet.
        """
        await self.driver.send_command_long(
            self, MAVCommand.REQUEST_AUTOPILOT_CAPABILITIES, param1=1
        )

        # At this point, we only received an acknowledgment from the drone that
        # it _will_ send the AUTOPILOT_VERSION packet -- we don't know whether
        # it really will and even if it does, it might get lost in transit.
        # Therefore, we check whether we already have an AUTOPILOT_VERSION
        # packet in our stash after receiving a heartbeat, and if we don't, we
        # ask the drone to send one by calling this function.

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

        are_motor_outputs_disabled = self._autopilot.are_motor_outputs_disabled(
            heartbeat, sys_status
        )
        is_prearm_check_in_progress = self._autopilot.is_prearm_check_in_progress(
            heartbeat, sys_status
        )

        errors = {
            FlockwaveErrorCode.AUTOPILOT_INIT_FAILED: (
                heartbeat.system_status == MAVState.UNINIT
            ),
            FlockwaveErrorCode.AUTOPILOT_INITIALIZING: (
                heartbeat.system_status == MAVState.BOOT
            ),
            FlockwaveErrorCode.UNSPECIFIED_ERROR: (
                heartbeat.system_status == MAVState.CRITICAL and not not_healthy_sensors
            ),
            FlockwaveErrorCode.UNSPECIFIED_CRITICAL_ERROR: (
                heartbeat.system_status == MAVState.EMERGENCY
                and not not_healthy_sensors
            ),
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
            FlockwaveErrorCode.DISARMED: are_motor_outputs_disabled,
            FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS: is_prearm_check_in_progress,
            # If the motors are running but we are not in the air yet; we use an
            # informational flag to let the user know
            FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND: (
                heartbeat.base_mode & MAVModeFlag.SAFETY_ARMED
                and heartbeat.system_status == MAVState.STANDBY
            ),
        }

        # Clear the collected prearm failure messages if the heartbeat and/or
        # the system status shows that we are not in the prearm check phase any
        # more
        if not is_prearm_check_in_progress:
            self._preflight_status.message = "Passed"
            self._preflight_status.result = PreflightCheckResult.PASS

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
