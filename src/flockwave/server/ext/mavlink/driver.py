"""Driver class for MAVLink-based drones."""

from __future__ import annotations

from collections import defaultdict
from colour import Color
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from logging import Logger
from math import inf, isfinite
from time import monotonic
from trio import Event, fail_after, move_on_after, sleep, TooSlowError
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Union

from flockwave.gps.time import datetime_to_gps_time_of_week, gps_time_of_week_to_utc
from flockwave.gps.vectors import GPSCoordinate, VelocityNED

from flockwave.concurrency import delayed, FutureCancelled
from flockwave.server.command_handlers import (
    create_calibration_command_handler,
    create_color_command_handler,
    create_parameter_command_handler,
    create_test_command_handler,
    create_version_command_handler,
)
from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.show.config import AuthorizationScope
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.commands import (
    Progress,
    ProgressEvents,
    ProgressEventsWithSuspension,
)
from flockwave.server.model.devices import DeviceTreeMutator
from flockwave.server.model.geofence import GeofenceConfigurationRequest, GeofenceStatus
from flockwave.server.model.gps import GPSFix, GPSFixType as OurGPSFixType
from flockwave.server.model.log import FlightLog, FlightLogMetadata
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.safety import SafetyConfigurationRequest
from flockwave.server.model.transport import TransportOptions
from flockwave.server.model.uav import (
    BulkParameterUploadResponse,
    VersionInfo,
    UAVBase,
    UAVDriver,
)
from flockwave.server.show import (
    get_altitude_reference_from_show_specification,
    get_coordinate_system_from_show_specification,
    get_geofence_configuration_from_show_specification,
    get_light_program_from_show_specification,
    get_trajectory_from_show_specification,
)
from flockwave.server.show.formats import SkybrushBinaryShowFile
from flockwave.server.types import GCSLogMessageSender
from flockwave.server.utils import color_to_rgb8_triplet, to_uppercase_string
from flockwave.server.utils.generic import nop
from flockwave.spec.errors import FlockwaveErrorCode

from .accelerometer import AccelerometerCalibration
from .autopilots import ArduPilot, Autopilot, UnknownAutopilot
from .comm import Channel
from .compass import CompassCalibration
from .enums import (
    ConnectionState,
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
    MAVType,
    MotorTestOrder,
    MotorTestThrottleType,
    PositionTargetTypemask,
    SkybrushUserCommand,
)
from .ftp import MAVFTP
from .log_download import MAVLinkLogDownloader
from .packets import (
    authorization_scope_to_int,
    create_led_control_packet,
    DroneShowExecutionStage,
    DroneShowStatus,
)
from .rssi import RSSIMode, rtcm_counter_to_rssi
from .types import MAVLinkMessage, PacketBroadcasterFn, PacketSenderFn, spec
from .utils import (
    can_communicate_infer_from_heartbeat,
    log_id_for_uav,
    mavlink_version_number_to_semver,
)


__all__ = ("MAVLinkDriver",)


#: Conversion constant from seconds to microseconds
SEC_TO_USEC = 1000000

#: Magic number to force an arming or disarming operation even if it is unsafe
#: to do so
FORCE_MAGIC = 21196

#: "Not a number" constant, used in some MAVLink messages to indicate a default
#: value
nan = float("nan")


def transport_options_to_channel(options: Optional[TransportOptions]) -> str:
    """Converts a transport options object sent by the user to a specific
    MAVLink channel that satisfies the transport options.

    We do not check whether the channel exists or not; it is the responsibility
    of the CommunicationManager to fall back to another channel if the specified
    channel is not open.
    """
    if options is not None and getattr(options, "channel", 0) > 0:
        return Channel.SECONDARY
    else:
        return Channel.PRIMARY


class MAVLinkDriver(UAVDriver["MAVLinkUAV"]):
    """Driver class for MAVLink-based drones."""

    assume_data_streams_configured: bool = False
    """Whether to assume that UAVs managed by this driver already have their
    MAVLink data streams configured appropriately. Used to skip the initialization
    part, which is useful if you have thousands of drones and you know that
    they are configured correctly.
    """

    autopilot_factory: Optional[Callable[[], Autopilot]] = None
    """Factory function that returns a new Autopilot_ instance to be used by
    drones managed by this driver. `None` means to infer the autopilot type
    automatically from the heartbeat and the autopilot capabilities. Used to
    skip the extra messages associated with the process, which is useful if you
    have thousands of drones and you know which autopilot they are using.
    """

    broadcast_packet: PacketBroadcasterFn
    """A function that should be called by the driver whenever it wants to
    broadcast a packet. The function must be called with the packet to send.
    """

    create_device_tree_mutator: Callable[[], DeviceTreeMutator]
    """A function that should be called by the driver as a context manager
    whenever it wants to mutate the state of the device tree.
    """

    log: Logger
    """Logger to use to write log messages."""

    mandatory_custom_mode: Optional[int]
    """Custom mode to switch drones to when they are seen for the first time."""

    run_in_background: Callable[[Callable[[], Awaitable[None]]], None]
    """A function that should be called by the driver whenever it wants to
    run an asynchronous function in the background.
    """

    send_packet: PacketSenderFn
    """A function that should be called by the driver whenever it wants to send
    a packet. The function must be called with the packet to send, and a pair
    formed by the medium via which the packet should be forwarded and the
    destination address in that medium.
    """

    use_bulk_parameter_uploads: bool = False
    """Whether to use bulk parameter uploads instead of individual uploads if
    the autopilot supports bulk uploads.
    """

    def __init__(self, app=None):
        """Constructor.

        Parameters:
            app: the app in which the driver lives
        """
        super().__init__()

        self.app = app  # type: ignore

        self.autopilot_factory = None
        self.broadcast_packet = None  # type: ignore
        self.create_device_tree_mutator = None  # type: ignore
        self.log = None  # type: ignore
        self.mandatory_custom_mode = None
        self.run_in_background = None  # type: ignore
        self.send_packet = None  # type: ignore

        self._default_timeout = 2
        self._default_retries = 10
        self._default_delay = 0.1

    async def broadcast_command_long_with_retries(
        self,
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
        delay: Optional[float] = None,
        channel: str = Channel.PRIMARY,
    ) -> None:
        """Broadcasts a MAVLink command to all UAVs reachable on a given
        communication channel a given number of times, without waiting for an
        acknowledgment.

        Due to the broadcasting nature of this command, we cannot wait for
        acknowledgments as we do not know how many acknowledgments we are
        expecting.

        Parameters:
            target: the UAV to send the command to
            param1: the first parameter of the command
            param2: the second parameter of the command
            param3: the third parameter of the command
            param4: the fourth parameter of the command
            param5: the fifth parameter of the command
            param6: the sixth parameter of the command
            param7: the seventh parameter of the command
            timeout: timeout in seconds for _sending_ the command; `None` means
                to use the default timeout for the driver
            retries: number of times the command will be sent, no matter whether
                a response is received or not; `None` means to use the default
                retry count for the driver
            delay: number of seconds to wait between sending attempts; `None`
                means to use the default setting for the driver
            channel: the channel to send the command on

        Raises:
            NotSupportedError: if the driver does not support broadcasting
        """
        if self.broadcast_packet is None:
            raise NotSupportedError("This driver does not support broadcasting")

        if timeout is None or timeout <= 0:
            timeout = self._default_timeout

        if retries is None or retries <= 0:
            retries = self._default_retries

        if delay is None or delay < 0:
            delay = self._default_delay

        tried, sent = 0, 0

        while tried < retries:
            try:
                with fail_after(timeout):
                    message = spec.command_long(
                        command=command_id,
                        param1=param1,
                        param2=param2,
                        param3=param3,
                        param4=param4,
                        param5=param5,
                        param6=param6,
                        param7=param7,
                        confirmation=0,
                        target_system=0,
                        target_component=0,
                    )
                    tried += 1
                    await self.broadcast_packet(message, channel=channel)
                    sent += 1
            except TooSlowError:
                pass

            if delay > 0:
                await sleep(delay)

        if sent < tried:
            if sent > 1:
                self.log.warning(
                    f"Tried to send broadcast command {tried} times but only "
                    f"{sent} were successful"
                )
            elif sent > 0:
                self.log.warning(
                    f"Tried to send broadcast command {tried} times but only "
                    f"one was successful"
                )
            elif tried > 1:
                self.log.warning(
                    f"Tried to send broadcast command {tried} times but none "
                    f"were successful"
                )
            else:
                self.log.warning("Failed to send broadcast command")

    def create_uav(self, id: str) -> "MAVLinkUAV":
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id: the identifier of the UAV to create

        Returns:
            MAVLinkUAV: an appropriate MAVLink UAV object
        """
        assert self.app is not None

        uav = MAVLinkUAV(id, driver=self)
        uav.notify_updated = partial(self.app.request_to_send_UAV_INF_message_for, [id])
        uav.send_log_message_to_gcs = partial(
            self.app.request_to_send_SYS_MSG_message, sender=id
        )
        return uav

    def get_time_boot_ms(self) -> int:
        """Returns a monotonic "time since boot" timestamp in milliseconds that
        can be used in MAVLink messages.
        """
        return int(monotonic() * 1000)

    handle_command_calib = create_calibration_command_handler(
        ("accel", "baro", "compass", "gyro", "level")
    )
    handle_command_color = create_color_command_handler()
    handle_command_param = create_parameter_command_handler(
        name_validator=to_uppercase_string
    )
    handle_command_test = create_test_command_handler(
        ("camera", "motor", "led", "pyro")
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

    async def handle_command_servo(
        self, uav: "MAVLinkUAV", servo: Union[int, str], value: Union[int, str]
    ):
        """Sets the value of a servo channel on the UAV.

        Parameters:
            servo: the servo channel to set (1-based)
            value: the value to set for the servo channel as a raw PWM value
        """
        servo = int(servo)
        value = int(value)
        await uav.set_servo(servo, value)
        return f"Servo {servo} set to {value}"

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
        elif command in ("clear", "remove"):
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
            self.log.error(str(ex), extra={"id": log_id_for_uav(uav)})
            raise
        except Exception as ex:
            self.log.error(str(ex), extra={"id": log_id_for_uav(uav)})
            raise

    async def send_command_int(
        self,
        target: "MAVLinkUAV",
        command_id: int,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        x: int = 0,
        y: int = 0,
        z: float = 0,
        *,
        frame: MAVFrame = MAVFrame.GLOBAL,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> bool:
        """Sends a MAVLink command to a given UAV and waits for an acknowlegment.
        Parameters 5 and 6 are integers; everything else is a float.

        Parameters:
            target: the UAV to send the command to
            param1: the first parameter of the command
            param2: the second parameter of the command
            param3: the third parameter of the command
            param4: the fourth parameter of the command
            x: the fifth parameter of the command
            y: the sixth parameter of the command
            z: the seventh parameter of the command
            frame: the reference frame of the coordinates transmitted in the command
            timeout: command timeout in seconds; `None` means to use the default
                timeout for the driver. Retries will be attempted if no response
                arrives to the command within the given time interval
            retries: maximum number of retries for the command (not counting the
                initial attempt); `None` means to use the default retry count
                for the driver.

        Returns:
            True if the command was sent successfully and a positive acknowledgment
            (`MAV_RESULT_ACCEPTED`) was received in time, False if the command
            was sent successfully and the response was not `MAV_RESULT_ACCEPTED`
            and not `MAV_RESULT_UNSUPPORTED`. An exception will be thrown if no
            acknowledgment is received in time (even after resending).

        Raises:
            TooSlowError: if the UAV failed to respond in time, even after
                re-sending the command as needed
            NotSupportedError: if the command is not supported by the UAV (i.e.
                we received a response with `MAV_RESULT_UNSUPPORTED`)
        """
        if timeout is None or timeout <= 0:
            timeout = self._default_timeout
        if retries is None or retries < 0:
            retries = self._default_retries

        result = None

        while retries >= 0:
            try:
                with fail_after(timeout):
                    message = spec.command_int(
                        frame=frame,
                        command=command_id,
                        current=0,
                        autocontinue=0,
                        param1=param1,
                        param2=param2,
                        param3=param3,
                        param4=param4,
                        x=x,
                        y=y,
                        z=z,
                    )
                    response = await self.send_packet(
                        message,
                        target,
                        wait_for_response=("COMMAND_ACK", {"command": command_id}),
                    )
                    assert response is not None
                    result = response.result
                    break
            except TooSlowError:
                retries -= 1

        if result is None:
            raise TooSlowError(f"No response received for command {command_id} in time")

        if result == MAVResult.UNSUPPORTED:
            raise NotSupportedError

        return result == MAVResult.ACCEPTED

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
        channel: str = Channel.PRIMARY,
        allow_in_progress: bool = False,
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
            channel: the channel to send the command on
            allow_in_progress: whether to treat MAVResult.IN_PROGRESS as a
                result value indicating success.

        Returns:
            True if the command was sent successfully and a positive acknowledgment
            (`MAV_RESULT_ACCEPTED`) was received in time, False if the command
            was sent successfully and the response was not `MAV_RESULT_ACCEPTED`
            and not `MAV_RESULT_UNSUPPORTED`. An exception will be thrown if no
            acknowledgment is received in time (even after resending).

        Raises:
            TooSlowError: if the UAV failed to respond in time, even after
                re-sending the command as needed
            NotSupportedError: if the command is not supported by the UAV (i.e.
                we received a response with `MAV_RESULT_UNSUPPORTED`)
        """
        if channel != Channel.PRIMARY:
            # We allow and expect ACKs only on the primary channel; the backup
            # channel is assumed to be one-way only so we fall back to the
            # non-ACKed version
            await self.send_command_long_without_ack(
                target,
                command_id,
                param1,
                param2,
                param3,
                param4,
                param5,
                param6,
                param7,
                timeout=timeout,
                channel=channel,
            )

            # Pretend that we have received an ACK
            return True

        if timeout is None or timeout <= 0:
            timeout = self._default_timeout
        if retries is None or retries < 0:
            retries = self._default_retries

        confirmation = 0
        result = None

        while retries >= 0:
            try:
                with fail_after(timeout):
                    message = spec.command_long(
                        command=command_id,
                        param1=param1,
                        param2=param2,
                        param3=param3,
                        param4=param4,
                        param5=param5,
                        param6=param6,
                        param7=param7,
                        confirmation=confirmation,
                    )
                    response = await self.send_packet(
                        message,
                        target,
                        wait_for_response=("COMMAND_ACK", {"command": command_id}),
                        channel=channel,
                    )
                    assert response is not None
                    result = response.result
                    break
            except TooSlowError:
                retries -= 1
                confirmation = 1

        if result is None:
            raise TooSlowError(f"No response received for command {command_id} in time")

        if result == MAVResult.UNSUPPORTED:
            raise NotSupportedError

        return result == MAVResult.ACCEPTED or (
            allow_in_progress and result == MAVResult.IN_PROGRESS
        )

    async def send_command_long_without_ack(
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
        channel: str = Channel.PRIMARY,
    ) -> None:
        """Sends a MAVLink command to a given UAV, without waiting for an
        acknowledgment.

        This function may be useful in one-way radio links where the UAV has
        no way to respond.

        Parameters:
            target: the UAV to send the command to
            param1: the first parameter of the command
            param2: the second parameter of the command
            param3: the third parameter of the command
            param4: the fourth parameter of the command
            param5: the fifth parameter of the command
            param6: the sixth parameter of the command
            param7: the seventh parameter of the command
            timeout: timeout in seconds for _sending_ the command; `None` means
                to use the default timeout for the driver
            channel: the channel to send the command on

        Raises:
            TooSlowError: if the link to the UAV failed to send the command in
                time, even after re-sending the command as needed
        """
        if timeout is None or timeout <= 0:
            timeout = self._default_timeout

        sent = False

        try:
            with fail_after(timeout):
                message = spec.command_long(
                    command=command_id,
                    param1=param1,
                    param2=param2,
                    param3=param3,
                    param4=param4,
                    param5=param5,
                    param6=param6,
                    param7=param7,
                    confirmation=0,
                )
                await self.send_packet(message, target, channel=channel)
                sent = True
        except TooSlowError:
            pass

        if not sent:
            raise TooSlowError(f"failed to send command {command_id} in time")

    async def send_packet_with_retries(
        self,
        spec,
        target,
        *,
        wait_for_response=None,
        wait_for_one_of=None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        channel: str = Channel.PRIMARY,
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
            channel: the channel to send the packet on

        Raises:
            TooSlowError: if the UAV failed to respond in time
        """
        if timeout is None or timeout <= 0:
            timeout = self._default_timeout
        if retries is None or retries < 0:
            retries = self._default_retries

        response = None

        while retries >= 0:
            try:
                with fail_after(timeout):
                    if wait_for_response is not None:
                        response = await self.send_packet(
                            spec,
                            target,
                            wait_for_response=wait_for_response,
                            channel=channel,
                        )
                    elif wait_for_one_of is not None:
                        response = await self.send_packet(
                            spec,
                            target,
                            wait_for_one_of=wait_for_one_of,
                            channel=channel,
                        )
                    else:
                        raise RuntimeError(
                            "At least one of 'wait_for_response' and 'wait_for_one_of' "
                            "must be provided"
                        )
                    break
            except TooSlowError:
                retries -= 1

        if response is None:
            raise TooSlowError("No response received for the outbound packet in time")

        return response

    async def _enter_low_power_mode_broadcast(
        self, *, transport: Optional[TransportOptions] = None
    ) -> None:
        channel = transport_options_to_channel(transport)
        await self.broadcast_command_long_with_retries(
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            param1=126,  # request low power mode
            channel=channel,
        )

    async def _enter_low_power_mode_single(
        self, uav: "MAVLinkUAV", *, transport: Optional[TransportOptions] = None
    ) -> None:
        # Effectively the same as shutdown, but without the attempt to stop the
        # motors (so drones where the motors are running will not be affected)
        channel = transport_options_to_channel(transport)
        if not await self.send_command_long(
            uav,
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            126,  # request low power mode
            channel=channel,
        ):
            raise RuntimeError("Failed to request low-power mode from autopilot")

    async def get_log(
        self, uav: "MAVLinkUAV", log_id: str
    ) -> ProgressEvents[Optional[FlightLog]]:
        try:
            log_number = int(log_id)
        except ValueError:
            raise RuntimeError(f"Invalid log ID: {log_id!r}") from None

        async for maybe_log_or_progress in uav.log_downloader.get_log(log_number):
            if maybe_log_or_progress is None:
                raise RuntimeError("No log with the given ID: {log_number!r}")
            else:
                yield maybe_log_or_progress

    async def _get_log_list_single(self, uav: "MAVLinkUAV") -> list[FlightLogMetadata]:
        return await uav.log_downloader.get_log_list()

    async def _get_parameter_single(self, uav: "MAVLinkUAV", name: str) -> float:
        return await uav.get_parameter(name)

    def _request_preflight_report_single(self, uav: "MAVLinkUAV") -> PreflightCheckInfo:
        return uav.preflight_status

    def _request_version_info_single(self, uav: "MAVLinkUAV") -> VersionInfo:
        return uav.get_version_info()

    async def _resume_from_low_power_mode_broadcast(
        self, *, transport: Optional[TransportOptions] = None
    ) -> None:
        # This is not supported by standard MAVLink so it relies on a custom
        # protocol extension
        channel = transport_options_to_channel(transport)
        await self.broadcast_command_long_with_retries(
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            param1=127,  # resume autopilot
            channel=channel,
        )
        # TODO(ntamas): shall we notify all the UAVs that they are about to
        # be resumed (i.e. _notify_rebooted_by_us())?

    async def _resume_from_low_power_mode_single(
        self, uav: "MAVLinkUAV", *, transport: Optional[TransportOptions] = None
    ) -> None:
        # This is not supported by standard MAVLink so it relies on a custom
        # protocol extension
        channel = transport_options_to_channel(transport)
        if not await self.send_command_long(
            uav,
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            127,  # resume autopilot
            channel=channel,
        ):
            raise RuntimeError("Failed to wake up autopilot from low-power mode")
        uav._notify_rebooted_by_us()

    async def _send_fly_to_target_signal_single(
        self, uav: "MAVLinkUAV", target: GPSCoordinate
    ) -> None:
        await uav.fly_to(target)

    async def _send_hover_signal_broadcast(self, *, transport=None) -> None:
        channel = transport_options_to_channel(transport)

        # TODO(ntamas): HACK HACK HACK This won't work for a PixHawk as we are
        # hardcoding the ArduCopter mode numbers here. If we wanted to do this
        # properly, we would not be able to broadcast because different UAVs
        # may have different autopilots and the mode numbers might be different.
        base_mode, mode, submode = MAVModeFlag.CUSTOM_MODE_ENABLED, 16, 0

        await self.broadcast_command_long_with_retries(
            MAVCommand.DO_SET_MODE,
            param1=float(base_mode),
            param2=float(mode),
            param3=float(submode),
            channel=channel,
        )

    async def _send_hover_signal_single(
        self, uav: "MAVLinkUAV", *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)
        await uav.set_mode("pos hold", channel=channel)

    async def _send_landing_signal_broadcast(self, *, transport=None) -> None:
        channel = transport_options_to_channel(transport)
        await self.broadcast_command_long_with_retries(
            MAVCommand.NAV_LAND, channel=channel
        )

    async def _send_landing_signal_single(
        self, uav: "MAVLinkUAV", *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)
        success = await self.send_command_long(
            uav, MAVCommand.NAV_LAND, channel=channel
        )
        if not success:
            raise RuntimeError("Landing command failed")

    async def _send_light_or_sound_emission_signal_broadcast(
        self, signals: list[str], duration: int, *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        if "light" in signals:
            message = create_led_control_packet(broadcast=True)
            await self.broadcast_packet(message, channel=channel)

    async def _send_light_or_sound_emission_signal_single(
        self, uav: "MAVLinkUAV", signals: list[str], duration: int, *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        if "light" in signals:
            message = create_led_control_packet()
            await self.send_packet(message, uav, channel=channel)

    async def _send_motor_start_stop_signal_broadcast(
        self, start: bool, force: bool = False, *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)
        await self.broadcast_command_long_with_retries(
            MAVCommand.COMPONENT_ARM_DISARM,
            1 if start else 0,
            FORCE_MAGIC if force else 0,
            channel=channel,
        )

    async def _send_motor_start_stop_signal_single(
        self, uav: "MAVLinkUAV", start: bool, force: bool = False, *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        if not await self.send_command_long(
            uav,
            MAVCommand.COMPONENT_ARM_DISARM,
            1 if start else 0,
            FORCE_MAGIC if force else 0,
            channel=channel,
        ):
            raise RuntimeError(
                "Failed to arm motors" if start else "Failed to disarm motors"
            )

    async def _send_reset_signal_broadcast(self, component, *, transport=None) -> None:
        channel = transport_options_to_channel(transport)

        if not component:
            # Resetting all the UAVs, this is supported
            await self.broadcast_command_long_with_retries(
                MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
                1,  # reboot autopilot
                channel=channel,
            )
            # TODO(ntamas): shall we notify all the UAVs that they are about to
            # be rebooted (i.e. _notify_rebooted_by_us())?
        else:
            # No per-component resets are implemented yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_reset_signal_single(
        self, uav: "MAVLinkUAV", component: str, *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        if not component:
            # Resetting the whole UAV, this is supported
            await uav.reboot(channel=channel)
        else:
            # No per-component resets are implemented on this UAV yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_return_to_home_signal_broadcast(self, *, transport=None) -> None:
        channel = transport_options_to_channel(transport)
        await self.broadcast_command_long_with_retries(
            MAVCommand.NAV_RETURN_TO_LAUNCH, channel=channel
        )

    async def _send_return_to_home_signal_single(
        self, uav: "MAVLinkUAV", *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        success = await self.send_command_long(
            uav, MAVCommand.NAV_RETURN_TO_LAUNCH, channel=channel
        )

        if not success:
            raise RuntimeError("Return to home command failed")

    async def _send_shutdown_signal_broadcast(self, *, transport=None) -> None:
        channel = transport_options_to_channel(transport)
        await self._send_motor_start_stop_signal_broadcast(
            start=False, force=True, transport=transport
        )
        await self.broadcast_command_long_with_retries(
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            2,  # shutdown autopilot
            channel=channel,
        )

    async def _send_shutdown_signal_single(
        self, uav: "MAVLinkUAV", *, transport=None
    ) -> None:
        channel = transport_options_to_channel(transport)

        await self._send_motor_start_stop_signal_single(
            uav, start=False, force=True, transport=transport
        )

        if not await self.send_command_long(
            uav,
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            2,  # shutdown autopilot
            channel=channel,
        ):
            raise RuntimeError("Failed to send shutdown command to autopilot")

    async def _send_takeoff_signal_single(
        self, uav: "MAVLinkUAV", *, scheduled: bool = False, transport=None
    ) -> None:
        if scheduled:
            # Ignore this; scheduled takeoffs are managed by the ScheduledTakeoffManager
            return

        channel = transport_options_to_channel(transport)

        await self._send_motor_start_stop_signal_single(
            uav, start=True, transport=transport
        )

        # Wait a bit to give the autopilot some time to start the motors, just
        # in case. Not sure whether this is needed.
        await sleep(0.1)

        # Send the takeoff command
        await uav.takeoff_to_relative_altitude(2.5, channel=channel)

    async def _set_parameter_single(
        self, uav: "MAVLinkUAV", name: str, value: Any
    ) -> None:
        try:
            value_as_float = float(value)
        except ValueError:
            raise RuntimeError(f"Value of parameter {name!r} must be numeric") from None
        await uav.set_parameter(name, value_as_float)

    async def _set_parameters_single(
        self, uav: "MAVLinkUAV", parameters: dict[str, Any]
    ) -> ProgressEvents[BulkParameterUploadResponse]:
        if self.use_bulk_parameter_uploads:
            parameters_as_float = {}
            for name, value in parameters.items():
                try:
                    value_as_float = float(value)
                except ValueError:
                    raise RuntimeError(
                        f"Value of parameter {name!r} must be numeric"
                    ) from None
                parameters_as_float[name] = value_as_float

            try:
                await uav.set_parameters(parameters_as_float)
            except Exception:
                if self.log:
                    self.log.exception("Failed to set parameters")
                yield {"success": False}
            else:
                yield {"success": True}

        else:
            async for event in super()._set_parameters_single(uav, parameters):
                yield event


@dataclass
class MAVLinkMessageRecord:
    """Simple object holding a pair of a MAVLink message and the corresponding
    monotonic timestamp when the message was observed.
    """

    message: MAVLinkMessage = None
    timestamp: float = 0

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


class MAVLinkUAV(UAVBase[MAVLinkDriver]):
    """Subclass for UAVs created by the driver for MAVLink-based drones."""

    notify_updated: Callable[[], None]
    send_log_message_to_gcs: GCSLogMessageSender

    _accelerometer_calibration: Optional[AccelerometerCalibration] = None
    """Accelerometer calibration status of the drone, constructed lazily.

    Use the `accelerometer_calibration` getter to access this property; this
    will ensure that the accelerometer calibration status object is created
    on-demand.
    """

    _battery: BatteryInfo
    """Battery status of the drone"""

    _compass_calibration: Optional[CompassCalibration] = None
    """Compass calibration status of the drone, constructed lazily.

    Use the `compass_calibration` getter to access this property; this will
    ensure that the compass calibration status object is created on-demand.
    """

    _connected_event: Event
    """Event that is emitted when the connection state of the UAV becomes
    connected.
    """

    _connection_state: ConnectionState = ConnectionState.DISCONNECTED
    """State of the connection to this drone."""

    _gps_fix: GPSFix
    """Current GPS fix status and position accuracy of the drone."""

    _last_messages: defaultdict[int, MAVLinkMessageRecord]
    """Stores a mapping of each MAVLink message type received from the drone
    to the most recent copy of the message of that type. Some of these
    records may be cleared when we don't detect a heartbeat from the
    drone any more.
    """

    _last_skybrush_status_info: Optional[DroneShowStatus] = None

    _log_downloader: Optional[MAVLinkLogDownloader] = None
    """Log downloader for the drone, constructed lazily.

    Use the `log_downloader` getter to access this property; this will
    ensure that the log downloader object is created on-demand.
    """

    _network_id: str = ""
    """Stores the MAVLink network ID of the drone (not part of the MAVLink
    messages; used by us to track which MAVLink network of ours the
    drone belongs to).
    """

    _system_id: int = 0
    """MAVLink system ID of the drone. Zero if unspecified or unknown."""

    _autopilot: Autopilot
    """Model of the autopilot used by this drone"""

    _battery: BatteryInfo
    """Battery status of the drone"""

    _configuring_data_streams: bool = False
    """Stores whether we are currently configuring the data stream rates for
    the drone. Used to avoid multiple parallel configuration attempts.
    """

    _first_connection: bool = True
    """Stores whether we are connecting to the drone for the first time;
    used to prevent a "probably rebooted warning" for the first connection.
    """

    _gps_fix: GPSFix
    """Current GPS fix status of the drone"""

    _last_autopilot_capabilities_requested_at: Optional[float] = None
    """Stores the time when we attempted to retrieve the autopilot capabilities
    for the last time. Used to avoid frequent requests.
    """

    _last_data_stream_configuration_attempted_at: Optional[float] = None
    """Stores the time when we attempted to configure the data streams for
    the last time. Used to avoid frequent reconfiguration attempts.
    """

    _last_skybrush_status_info: Optional[DroneShowStatus] = None
    """The last Skybrush-specific status packet received from the UAV if it ever
    sent one.
    """

    _preflight_status: PreflightCheckInfo
    """The status of the preflight checks on the drone"""

    _position: GPSCoordinate
    """The current global position of the drone"""

    _rssi_mode: RSSIMode
    """Specifies how the RSSI values of the communication channels of the drone
    should be calculated.
    """

    _scheduled_takeoff_authorization_scope: AuthorizationScope = AuthorizationScope.NONE
    """The current authorization scope of the scheduled takeoff of the drone."""

    _scheduled_takeoff_time: Optional[int] = None
    """Scheduled takeoff time of the drone, as a UNIX timestamp, in seconds"""

    _scheduled_takeoff_time_gps_time_of_week: Optional[int] = None
    """Scheduled takeoff time of the drone, as a GPS time-of-week timestamp,
    in seconds"""

    _velocity: VelocityNED
    """Current velocity of the drone in NED coordinate system, m/sec"""

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        make_autopilot = self.driver.autopilot_factory or UnknownAutopilot
        self._autopilot = make_autopilot()

        self._battery = BatteryInfo()
        self._connected_event = Event()
        self._gps_fix = GPSFix()
        self._last_messages = defaultdict(MAVLinkMessageRecord)  # type: ignore
        self._preflight_status = PreflightCheckInfo()
        self._position = GPSCoordinate()
        self._rssi_mode = RSSIMode.NONE
        self._velocity = VelocityNED()

        self.notify_updated = None  # type: ignore
        self.send_log_message_to_gcs = nop

        self._reset_mavlink_version()

    def assign_to_network_and_system_id(self, network_id: str, system_id: int) -> None:
        """Assigns the UAV to the MAVLink network with the given network ID.
        The UAV is assumed to have the given system ID in the given network, and
        it is assumed to have a component ID of 1 (primary autopilot). We are
        not talking to any other component of a MAVLink system yet.
        """
        if self._network_id:
            raise RuntimeError(
                f"This UAV is already a member of MAVLink network {self._network_id}"
            )
        elif not network_id:
            raise RuntimeError("MAVLink network ID may not be empty")

        self._network_id = network_id
        self._system_id = system_id

    async def calibrate_accelerometer(self) -> ProgressEventsWithSuspension[None, str]:
        """Calibrates the accelerometers of the UAV.

        Yields:
            events describing the progress of the calibration

        Raises:
            NotSupportedError: if the accelerometer calibration is not supported
                on the UAV
        """
        try:
            async for event in self._autopilot.calibrate_accelerometer(self):  # type: ignore
                yield event
        except NotImplementedError:
            # Turn NotImplementedError from the autopilot into a NotSupportedError
            raise NotSupportedError from None

    async def calibrate_compass(self) -> ProgressEventsWithSuspension[None, str]:
        """Calibrates the compasses of the UAV.

        Yields:
            events describing the progress of the calibration

        Raises:
            NotSupportedError: if the compass calibration is not supported on
                the UAV
        """
        try:
            async for event in self._autopilot.calibrate_compass(self):  # type: ignore
                yield event
        except NotImplementedError:
            # Turn NotImplementedError from the autopilot into a NotSupportedError
            raise NotSupportedError from None

    async def calibrate_component(
        self, component: str
    ) -> ProgressEventsWithSuspension[None, str]:
        """Calibrates a component of the UAV.

        Parameters:
            component: the component to calibrate; currently we support
                ``accel``, ``baro``, ``compass``, ``gyro`` or ``level``.

        Raises:
            NotSupportedError: if the calibration of the given component is not
                supported on this UAV
            RuntimeError: if the UAV rejected to calibrate the component
        """
        if component == "accel":
            # Acceleration calibration takes a long time, needs user input and
            # involves progress handling so that's handled in a separate function
            async for event in self.calibrate_accelerometer():
                yield event
            return

        if component == "compass":
            # Compass calibration takes a long time and involves progress
            # handling so that's handled in a separate function
            async for event in self.calibrate_compass():
                yield event
            return

        # all other calibration procedures are handled with a single command
        params = [0] * 7
        if component == "baro":
            params[2] = 1
        elif component == "gyro":
            params[0] = 1
        elif component == "level":
            params[4] = 2
        else:
            raise NotSupportedError

        # In ArduCopter 4.5 (and maybe in other versions), the baro calibration
        # returns MAVResult.IN_PROGRESS if there is an airspeed sensor, which
        # we need to treat as a successful calibration.

        success = await self.driver.send_command_long(
            self,
            MAVCommand.PREFLIGHT_CALIBRATION,
            *params,
            allow_in_progress=(component == "baro"),
        )

        if not success:
            raise RuntimeError(f"Failed to calibrate component: {component!r}")

    def can_handle_firmware_update_target(self, target_id: str) -> bool:
        """Returns whether the virtual UAV can handle uploads with the given
        target.
        """
        return self._autopilot.can_handle_firmware_update_target(target_id)

    async def clear_scheduled_takeoff_time(self) -> None:
        """Clears the scheduled takeoff time of the UAV."""
        await self.set_scheduled_takeoff_time(None)

    async def configure_geofence(
        self, configuration: GeofenceConfigurationRequest
    ) -> None:
        """Configures the geofence on the UAV."""
        return await self._autopilot.configure_geofence(self, configuration)

    async def configure_safety(self, configuration: SafetyConfigurationRequest) -> None:
        """Configures the safety features on the UAV."""
        return await self._autopilot.configure_safety(self, configuration)

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

        if version_info is not None and version_info.board_version > 0:
            result["board"] = mavlink_version_number_to_semver(
                version_info.board_version
            )

        return result

    async def flash_led(self, *, channel: str = Channel.PRIMARY) -> None:
        """Flashes the LED of the drone.

        Parameters:
            channel: the communication channel to send the command on
        """
        message = create_led_control_packet()
        await self.driver.send_packet(message, self, channel=channel)

    async def fly_to(self, target: GPSCoordinate) -> None:
        """Sends a command to the UAV to reposition it to the given coordinate,
        where the altitude may be specified in AMSL or AHL.
        """
        if self._autopilot.supports_repositioning:
            # Implementation of fly_to() with the MAVLink DO_REPOSITION command
            await self._fly_to_with_repositioning(target)
        else:
            # Implementation of fly_to() with a guided mode command
            await self._fly_to_in_guided_mode(target)

    async def _fly_to_in_guided_mode(self, target: GPSCoordinate) -> None:
        """Implementation of `fly_to()` using a MAVLink
        SET_POSITION_TARGET_GLOBAL_INT guided mode message.
        """
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
            if target.ahl is None:
                # We cannot simply set Z_IGNORE in the type mask because that
                # does not work with ArduCopter (it would ignore the whole
                # position).
                altitude = self.status.position.ahl
            else:
                altitude = target.ahl
        else:
            frame = MAVFrame.GLOBAL_INT
            altitude = target.amsl

        lat, lon = int(target.lat * 1e7), int(target.lon * 1e7)

        message = spec.set_position_target_global_int(
            time_boot_ms=self.driver.get_time_boot_ms(),
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

        # There is usually no confirmation for the guided mode command, unless
        # the drone streams us the POSITION_TARGET_GLOBAL_INT message, which it
        # does not necessarily do. So, we send the packet five times, with
        # 200 msec between attempts, and wait for a matching POSITION_TARGET_GLOBAL_INT
        # message, but we don't freak out if we don't get any response.
        response = spec.position_target_global_int(
            # position
            lat_int=lat,
            lon_int=lon,
            # note that we don't check the altitude in the response because the
            # position target feedback could come in AMSL or AHL
        )
        try:
            await self.driver.send_packet_with_retries(
                message, self, wait_for_response=response, timeout=0.2, retries=4
            )
        except TooSlowError:
            # Maybe it's okay anyway, see comment above
            pass

    async def _fly_to_with_repositioning(self, target: GPSCoordinate) -> None:
        """Implementation of `fly_to()` using a MAVLink DO_REPOSITION command
        with proper confirmation.
        """
        # PX4 supports AMSL only so we always convert to AMSL; NaN means to
        # hold the current altitude
        if target.amsl is not None:
            altitude = target.amsl
        else:
            altitude = (
                self.convert_ahl_to_amsl(target.ahl) if target.ahl is not None else nan
            )

        lat, lon = int(target.lat * 1e7), int(target.lon * 1e7)

        success = await self.driver.send_command_int(
            self,
            MAVCommand.DO_REPOSITION,
            frame=MAVFrame.GLOBAL_INT,
            param1=-1,  # speed (default)
            param2=0,  # flags
            param3=0,  # reserved
            param4=nan,  # yaw mode
            x=lat,  # latitude
            y=lon,  # longitude
            z=altitude,  # altitude
        )

        if not success:
            raise RuntimeError("Fly to waypoint command failed")

    @property
    def scheduled_takeoff_authorization_scope(self) -> AuthorizationScope:
        """Returns whether the UAV is authorized to do a scheduled takeoff and
        if so, what the scope of the authorization is.
        """
        return self._scheduled_takeoff_authorization_scope

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

    async def _set_parameter_single(self, name: str, value: float) -> None:
        """Sets the value of a single parameter on the UAV.

        This function assumes that all sanity checks on the name and the value
        have already been performed by the caller.
        """
        # We need to retrieve the current value of the parameter first because
        # we need its type
        param_id = name.encode("utf-8")[:16]
        response = await self._get_parameter(name)
        param_type = response.param_type
        encoded_value = self._autopilot.encode_param_to_wire_representation(
            value, param_type
        )

        try:
            # Try to set the new parameter value. In normal circumstances, we
            # will get a PARAM_VALUE message in response, with the same
            # parameter ID. However, in ArduPilot, when SERIALx_OPTIONS bit 10
            # is set for the primary telemetry channel, we will _not_ get
            # PARAM_VALUE messages, at least not in ArduPilot 4.4. In this case,
            # we make one final attempt to read the parameter value explicitly.
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

        except TooSlowError:
            # This is where we try to recover
            observed_value = await self.get_parameter(name)
            if value != observed_value:
                raise RuntimeError(
                    f"Failed to set parameter {name!r}, "
                    f"tried to set {value!r}, got {observed_value!r}"
                ) from None

    async def set_parameter(self, name: str, value: float) -> None:
        """Sets the value of a single parameter on the UAV."""
        return await self.set_parameters({name: value})

    async def set_parameters(self, parameters: dict[str, float]) -> None:
        """Sets the value of multiple parameters on the UAV, preferably in a
        more efficient manner if the autopilot of the drone supports MAVFTP
        parameter uploads.
        """
        if not parameters:
            return

        # Basic sanity check on the values
        for name, value in parameters.items():
            if not isfinite(value):
                raise RuntimeError(f"Value of parameter {name!r} must be finite")

        if len(parameters) > 1 and self._autopilot.supports_mavftp_parameter_upload:
            # Do a bulk upload
            async with aclosing(MAVFTP.for_uav(self)) as ftp:
                filename, contents = self._autopilot.prepare_mavftp_parameter_upload(
                    parameters
                )
                # TODO(ntamas): handle error code when closing the file
                await ftp.put(contents, filename, skip_crc_check=True)

        else:
            # No support for bulk uploads, or we only have a single parameter,
            # so just do it one by one
            for name, value in sorted(parameters.items()):
                await self._set_parameter_single(name, value)

    async def test_component(
        self, component: str, *, channel: str = Channel.PRIMARY
    ) -> None:
        """Tests a component of the UAV.

        Parameters:
            component: the component to test; currently we support ``camera``,
                ``led``, ``motor`` and ``pyro``
            channel: the communication channel to use when sending the command
        """
        if component == "motor":
            # Older versions of ArduCopter did not support the motor count
            # parameter so let's just test all the motors one by one
            heartbeat = self.get_last_message(MAVMessageType.HEARTBEAT)
            motor_count = 4 if not heartbeat else MAVType(heartbeat.type).motor_count
            for i in range(motor_count):
                await self.driver.send_command_long(
                    self,
                    MAVCommand.DO_MOTOR_TEST,
                    i + 1,  # motor instance number
                    float(MotorTestThrottleType.PERCENT),
                    15,  # 15%
                    2,  # timeout: 2 seconds
                    0,  # 1 motor only
                    float(MotorTestOrder.DEFAULT),
                    channel=channel,
                )
                await sleep(3)

        elif component == "led":
            color_sequence = [
                Color(name)
                for name in "red lime blue yellow cyan magenta white".split()
            ]
            for index, color in enumerate(color_sequence):
                if index > 0:
                    await sleep(1)
                await self.set_led_color(color, channel=channel, duration=2)

        elif component == "pyro":
            await self.start_pyro_test()

        elif component == "camera":
            await self.trigger_camera_shutter()

        else:
            raise NotSupportedError

    async def handle_firmware_update(
        self, target_id: str, blob: bytes
    ) -> AsyncIterator[Progress]:
        async for event in self._autopilot.handle_firmware_update(
            self, target_id, blob
        ):
            yield event

        self._clear_autopilot_capabilities()

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

    def handle_message_command_long(self, message: MAVLinkMessage):
        if message.command == MAVCommand.ACCELCAL_VEHICLE_POS:
            self.accelerometer_calibration.handle_message_accelcal_vehicle_pos(message)
        else:
            # Do not raise an exception here, otherwise it would be an easy way
            # to crash the extension -- just send an unsupported COMMAND_LONG
            pass

    def handle_message_drone_show_status(self, message: MAVLinkMessage):
        """Handles an incoming drone show specific status message targeted at
        this UAV.
        """
        data = DroneShowStatus.from_mavlink_message(message)

        self._last_skybrush_status_info = data

        # Process the basic part of the packet that is always present (both with
        # the standard and the compact telemetry profile)
        self._update_gps_fix_type_and_satellite_count(data.gps_fix, data.num_satellites)

        gps_start_time = data.start_time if data.start_time >= 0 else None
        if gps_start_time != self._scheduled_takeoff_time_gps_time_of_week:
            self._scheduled_takeoff_time_gps_time_of_week = gps_start_time
            if gps_start_time is None:
                self._scheduled_takeoff_time = None
            else:
                self._scheduled_takeoff_time = int(
                    gps_time_of_week_to_utc(gps_start_time).timestamp()
                )

        self._scheduled_takeoff_authorization_scope = data.authorization_scope

        debug = data.message.encode("utf-8")

        self._update_errors_from_drone_show_status_packet(data)

        updates = {"light": data.light, "gps": self._gps_fix, "debug": debug}

        if self._rssi_mode is RSSIMode.RTCM_COUNTERS:
            updates["rssi"] = [
                rtcm_counter_to_rssi(data.rtcm_counters[0]),
                rtcm_counter_to_rssi(data.rtcm_counters[1]),
            ]

        # If the status packet has an extended section, process the extended part
        if data.extension:
            extended_part = data.extension

            # Process the missing information that the standard telemetry
            # provides with the GLOBAL_POSITION_INT packet
            self._position.lat = extended_part.lat
            self._position.lon = extended_part.lng
            self._position.amsl = extended_part.alt
            self._position.ahl = extended_part.relative_alt
            self._velocity.x = extended_part.vx
            self._velocity.y = extended_part.vy
            self._velocity.z = extended_part.vz

            # Process the missing information that the standard telemetry
            # provides with the GPS_RAW_INT packet
            self._gps_fix.horizontal_accuracy = extended_part.h_acc
            self._gps_fix.vertical_accuracy = extended_part.v_acc

            updates["position"] = self._position
            updates["velocity"] = self._velocity
            updates["heading"] = extended_part.heading

        self.update_status(**updates)

        self.notify_updated()

    def handle_message_heartbeat(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        if self._mavlink_version < 2 and message.get_msgbuf()[0] == 253:
            # Other side sent a MAVLink 2 heartbeat so we can switch to MAVLink
            # 2 as well
            self._mavlink_version = 2

        # Get the age of the _last_ heartbeat, will be used later
        age_of_last_heartbeat = self.get_age_of_message(MAVMessageType.HEARTBEAT)

        # Store a copy of the heartbeat
        self._store_message(message)

        # Determine whether the heartbeat indicates that it makes sense trying
        # to initiate communication with the UAV. Heartbeats that indicate that
        # the UAV is powering down or is completely powered down will not trigger
        # further communication with the UAV.
        can_communicate = can_communicate_infer_from_heartbeat(message)

        # If the heartbeat indicates that we might not be able to communicate
        # with the drone yet (maybe the heartbeat is sent by the wifi module on
        # behalf of the drone while the FC is off), let's not initiate
        # communication but just keep track of whatever we've found in the
        # heartbeat
        if not can_communicate:
            self._update_errors_from_inactive_state()
            self.update_status(mode="off")
            self.notify_updated()
            return

        if self._connection_state is not ConnectionState.CONNECTED:
            self.notify_reconnection(message)

        # Do we already have basic information about the autopilot capabilities?
        # If we don't, ask for them.
        if not self.driver.autopilot_factory and not self.get_last_message(
            MAVMessageType.AUTOPILOT_VERSION
        ):
            now = monotonic()
            if (
                self._last_autopilot_capabilities_requested_at is None
                or now - self._last_autopilot_capabilities_requested_at > 2
            ):
                self._last_autopilot_capabilities_requested_at = now
                self.driver.run_in_background(self._request_autopilot_capabilities)

        # If we haven't received a SYS_STATUS message for a while but we keep
        # on receiving heartbeats, chances are that the data streams are not
        # configured correctly so we configure them.
        if (
            not self.driver.assume_data_streams_configured
            and age_of_last_heartbeat < 2
            and self.get_age_of_message(MAVMessageType.SYS_STATUS) > 5
        ):
            self._configure_data_streams_soon()

        # Update error codes and basic status info
        self._update_errors_from_sys_status_and_heartbeat()
        self.update_status(
            mode=self._autopilot.describe_mode(message.base_mode, message.custom_mode)
        )
        self.notify_updated()

    def handle_message_global_position_int(self, message: MAVLinkMessage):
        # TODO(ntamas): reboot detection with time_boot_ms

        if abs(message.lat) <= 900000000:
            self._position.lat = message.lat / 1e7
            self._position.lon = message.lon / 1e7
            self._position.amsl = message.alt / 1e3
            self._position.ahl = message.relative_alt / 1e3
        else:
            # Some drones, such as the Parrot Bebop 2, use 2^31-1 as latitude
            # and longitude to indicate that no GPS fix has been obtained yet,
            # so treat any values outside the valid latitude range as invalid
            self._position.lat = self._position.lon = self._position.amsl = (
                self._position.ahl
            ) = 0

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

        self._gps_fix.horizontal_accuracy = (
            message.h_acc / 1000.0 if message.h_acc > 0 else None
        )
        self._gps_fix.vertical_accuracy = (
            message.v_acc / 1000.0 if message.v_acc > 0 else None
        )
        self._update_gps_fix_type_and_satellite_count(
            GPSFixType(message.fix_type).to_ours(), num_sats if num_sats < 255 else None
        )

        self.update_status(gps=self._gps_fix)
        self.notify_updated()

    def handle_message_log_data(self, message: MAVLinkMessage):
        self.log_downloader.handle_message_log_data(message)

    def handle_message_log_entry(self, message: MAVLinkMessage):
        self.log_downloader.handle_message_log_entry(message)

    def handle_message_mag_cal_progress(self, message: MAVLinkMessage):
        self.compass_calibration.handle_message_mag_cal_progress(message)

    def handle_message_mag_cal_report(self, message: MAVLinkMessage):
        self.compass_calibration.handle_message_mag_cal_report(message)

    def handle_message_radio_status(self, message: MAVLinkMessage):
        if self._rssi_mode is not RSSIMode.RADIO_STATUS:
            return

        # Limitations:
        # - Currently we do not account for multiple connections; we always
        #   update the RSSI of the first connection. This is fine for the time
        #   being because we assume that the wifi connection (which provides
        #   the RSSI value) is the first connection, and the radio connection is
        #   not supposed to be bi-directional.
        # - We handle the conventions of the MAVESP8266 firmware only.
        #   MAVESP8266 simply uses WiFi.RSSI(), which is a 8-bit signed int
        #   representing the signal level in dBm. We scale the value to a
        #   0% - 100% range such that anything less than -100 dBm is 0% and
        #   anything larger than -50 dBm is 100%.
        # - In AP mode, the MAVESP8266 firmware transmits 0 as the RSSI value,
        #   which is interpreted as 0 dBm here and translated to 200%, which then
        #   gets clamped to 100%.

        # Unsigned-to-signed conversion to work around MAVLink data type issues
        rssi_dbm = message.rssi if message.rssi <= 127 else message.rssi - 256
        rssi = min(max(0, int((rssi_dbm + 100) * 2)), 100)
        self.update_rssi(index=0, value=rssi)

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
    def accelerometer_calibration(self) -> AccelerometerCalibration:
        """State object of the accelerometer calibration procedure."""
        if self._accelerometer_calibration is None:
            self._accelerometer_calibration = AccelerometerCalibration()
        return self._accelerometer_calibration

    @property
    def compass_calibration(self) -> CompassCalibration:
        """State object of the compass calibration procedure."""
        if self._compass_calibration is None:
            self._compass_calibration = CompassCalibration()
        return self._compass_calibration

    @property
    def is_connected(self) -> bool:
        """Returns whether the UAV is connected to the ground station and we
        have seen heartbeats from it recently (even if they indicated that the
        drone is in a sleep state).
        """
        return self._connection_state is not ConnectionState.DISCONNECTED

    @property
    def log_downloader(self) -> MAVLinkLogDownloader:
        """State object of the log download procedure."""
        if self._log_downloader is None:
            self._log_downloader = MAVLinkLogDownloader.for_uav(self)
        return self._log_downloader

    @property
    def mavlink_version(self) -> int:
        """The MAVLink version supported by this UAV."""
        return self._mavlink_version

    @property
    def network_id(self) -> str:
        """The network ID of the UAV."""
        return self._network_id

    @property
    def system_id(self) -> int:
        """The system ID of the UAV."""
        return self._system_id

    def notify_disconnection(self) -> None:
        """Notifies the UAV state object that we have detected that it has been
        disconnected from the network. In other words, the heartbeats from the
        drone have ceased arriving.
        """
        self._set_connection_state(ConnectionState.DISCONNECTED, None)

    def _notify_rebooted_by_us(self) -> None:
        """Notifies the UAV state object that we have rebooted the UAV ourselves
        and we should configure its data streams again soon once we re-establish
        connection.
        """
        self.driver.run_in_background(
            delayed(1, self.notify_disconnection, ensure_async=True)  # type: ignore[reportArgumentType]
        )

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

    def notify_reconnection(self, heartbeat: MAVLinkMessage) -> None:
        """Notifies the UAV state object that it has been reconnected to the
        network.
        """
        if can_communicate_infer_from_heartbeat(heartbeat):
            new_state = ConnectionState.CONNECTED
        else:
            new_state = ConnectionState.SLEEPING
        self._set_connection_state(new_state, heartbeat)

    @property
    def preflight_status(self) -> PreflightCheckInfo:
        return self._preflight_status

    async def reboot(self, channel: str = Channel.PRIMARY) -> None:
        """Reboots the autopilot of the UAV."""
        success = await self.driver.send_command_long(
            self,
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            1,  # reboot autopilot
            channel=channel,
        )
        if not success:
            raise RuntimeError("Reset command failed")
        else:
            self._notify_rebooted_by_us()

    async def reboot_after_update(self, channel: str = Channel.PRIMARY) -> None:
        """Reboots the autopilot of the UAV and keeps it in the bootloader
        until upgraded.

        This function should be called after an over-the-air update if the UAV
        supports over-the-air updates.
        """
        success = await self.driver.send_command_long(
            self,
            MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN,
            3,  # reboot autopilot and stay in bootloader until updated
            channel=channel,
        )
        if not success:
            raise RuntimeError("Reset and update command failed")
        else:
            self._notify_rebooted_by_us()

    async def reload_show(self) -> None:
        """Asks the UAV to reload the current drone show file."""
        # param1 = 0 if we want to reload the show file
        success = await self.driver.send_command_long(
            self, MAVCommand.USER_1, SkybrushUserCommand.RELOAD_SHOW
        )
        if not success:
            raise RuntimeError("Failed to reload show file")

    async def remove_show(self) -> None:
        """Asks the UAV to remove the current drone show file."""
        # param1 = 1 if we want to clear the show file
        success = await self.driver.send_command_long(
            self, MAVCommand.USER_1, SkybrushUserCommand.REMOVE_SHOW
        )
        if not success:
            raise RuntimeError("Failed to remove show file")

    async def set_mode(
        self, mode: Union[int, str], *, channel: str = Channel.PRIMARY
    ) -> None:
        """Attempts to set the UAV in the given custom mode."""
        if isinstance(mode, str):
            try:
                mode = int(mode)
            except ValueError:
                pass

        if isinstance(mode, int):
            base_mode, submode = MAVModeFlag.CUSTOM_MODE_ENABLED, 0
        elif isinstance(mode, str):
            try:
                base_mode, mode, submode = self._autopilot.get_flight_mode_numbers(mode)
            except NotSupportedError:
                raise ValueError(
                    "setting flight modes by name is not supported"
                ) from None
        else:
            raise TypeError("flight mode must be numeric or string")

        success = await self.driver.send_command_long(
            self,
            MAVCommand.DO_SET_MODE,
            param1=float(base_mode),
            param2=float(mode),
            param3=float(submode),
            channel=channel,
        )

        if not success:
            raise RuntimeError(f"UAV rejected flight mode {mode}")

    async def set_servo(
        self, servo: int, value: int, *, channel: str = Channel.PRIMARY
    ) -> None:
        """Asks the UAV to set one of its servo channels to a given value.

        Args:
            servo: the index of the servo channel (1-based)
            value: the PWM value to set on the servo channel, in microseconds
        """
        if servo < 1:
            raise RuntimeError("Invalid servo channel index")
        if value < 0:
            raise RuntimeError("Invalid servo channel value")

        success = await self.driver.send_command_long(
            self,
            MAVCommand.DO_SET_SERVO,
            param1=float(servo),
            param2=float(value),
            channel=channel,
        )

        if not success:
            raise RuntimeError(f"UAV rejected setting servo channel {servo} to {value}")

    @property
    def supports_scheduled_takeoff(self) -> bool:
        """Returns whether the UAV supports scheduled takeoffs."""
        return self._autopilot and self._autopilot.supports_scheduled_takeoff

    async def set_authorization_scope(self, scope: AuthorizationScope) -> None:
        """Sets or clears whether the UAV has authorization to perform an
        automatic takeoff.
        """
        await self.set_parameter("SHOW_START_AUTH", authorization_scope_to_int(scope))

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

    async def set_led_color(
        self,
        color: Optional[Color],
        *,
        channel: str = Channel.PRIMARY,
        duration: float = 5,
    ) -> None:
        """Sets the color of the drone LED to a specific RGB color."""
        if color is not None:
            red, green, blue = color_to_rgb8_triplet(color)
            duration_msec = max(0, min(int(duration * 1000), 65535))
            effect = 1
        else:
            red, green, blue = 0, 0, 0
            duration_msec = 0
            effect = 0

        message = create_led_control_packet(
            [
                red,
                green,
                blue,
                duration_msec & 0xFF,
                duration_msec >> 8,
                effect,
            ]
        )
        await self.driver.send_packet(message, self, channel=channel)

    async def start_pyro_test(
        self, channel: Optional[Union[int, tuple[int, int]]] = None, delay: float = 2
    ) -> None:
        """Asks the UAV to start testing its pyro channels.

        Args:
            channel: the channel index to test or the channels to test. ``None``
                means to test all channels.
            delay: time between tests of consecutive channels, in seconds
        """
        if channel is None:
            start, end = 0, 256
        elif isinstance(channel, int):
            start, end = channel, channel + 1
        else:
            start, end = channel

        num_channels = end - start
        if num_channels <= 0:
            return

        success = await self.driver.send_command_long(
            self,
            MAVCommand.USER_1,
            SkybrushUserCommand.TEST_PYRO,
            start,
            num_channels,
            delay,
        )
        if not success:
            raise RuntimeError("Failed to start pyro test")

    async def takeoff_to_relative_altitude(
        self, altitude: float = 2.5, *, channel: str = Channel.PRIMARY
    ) -> None:
        """Instructs the UAV to take off to a relative altitude above its
        current position.

        Parameters:
            altitude: the relative altitude above the current position of the
                UAV where we should take off to
            channel: the channel to use to send the command

        Raises:
            RuntimeError: if the command cannot be sent to the UAV or if it does
                not acknowledge the takeoff command
        """
        # Okay, so the NAV_TAKEOFF command sucks big time. ArduCopter interprets
        # the last argument as relative altitude above the ground, which totally
        # makes sense. PX4 interprets it as _absolute_ AMSL instead, and treats
        # NaN as "just pick a sensible takeoff altitude". Furthermore, ArduCopter
        # ignores the latitude and longitude while PX4 insists them to be NaN
        # (otherwise they are treated as coordinates to take off to). This stems
        # from the fact that MAV reference frames are not supported in PX4:
        #
        # https://github.com/PX4/PX4-Autopilot/issues/10246
        #
        # The lowest common denominator is to send NaN as the latitude and
        # longitude, and make the takeoff altitude dependent on whether the
        # autopilot supports the local reference frame. However, the ArduCopter
        # SITL simulator blows up when we do so -- so for ArduCopter, we send
        # zeros instead.
        if not self._autopilot.supports_local_frame:
            try:
                # We assume that we are at zero meters AHL
                altitude = self.convert_ahl_to_amsl(altitude, current_ahl=0)
            except RuntimeError:
                # No position yet, just send NaN and hope for the best
                altitude = nan

        # set takeoff coordinate. PX4 needs NaN / NaN, ArduPilot needs 0 / 0
        lat, lon = nan, nan
        if isinstance(self._autopilot, ArduPilot):
            lat, lon = 0, 0

        if not await self.driver.send_command_long(
            self,
            MAVCommand.NAV_TAKEOFF,
            param4=nan,  # yaw should stay the same
            param5=lat,  # latitude
            param6=lon,  # longitude
            param7=altitude,  # takeoff altitude
            channel=channel,
        ):
            raise RuntimeError("Failed to send takeoff command")

    @asynccontextmanager
    async def temporarily_request_messages(
        self, messages: dict[int, float]
    ) -> AsyncIterator[None]:
        """Temporarily requests the UAV to send a given set of messages while
        the execution is in the context, resetting the messages upon exiting
        the context. Resetting is done at a best-effort basis; failures will be
        ignored.

        Parameters:
            messages: a dict mapping MAVLink message IDs to their corresponding
                stream rates in Hz
        """
        successful = []
        try:
            for message, rate in messages.items():
                success = await self.driver.send_command_long(
                    self,
                    MAVCommand.SET_MESSAGE_INTERVAL,
                    message,
                    1000000 / rate,  # one per second
                )
                if success:
                    successful.append(message)
                else:
                    raise RuntimeError(
                        f"UAV rejected message stream rate of {rate} Hz for "
                        f"message {message}"
                    )
            yield
        finally:
            failed = []
            for message in successful:
                try:
                    await self.driver.send_command_long(
                        self,
                        MAVCommand.SET_MESSAGE_INTERVAL,
                        message,
                        0,  # off
                    )
                except Exception:
                    failed.append(message)

            for message in failed:
                self.driver.log.warning(
                    f"Failed to reset data stream rate(s) for message(s) {message}",
                    extra={"id": log_id_for_uav(self)},
                )

    async def trigger_camera_shutter(self) -> None:
        """Asks the UAV to trigger the camera shutter (if it has a camera)."""
        success = await self.driver.send_command_long(
            self,
            MAVCommand.DO_DIGICAM_CONTROL,
            param5=1,
        )
        if not success:
            raise RuntimeError("Failed to trigger camera shutter")

    async def upload_show(self, show) -> None:
        coordinate_system = get_coordinate_system_from_show_specification(show)
        if coordinate_system.type != "nwu":
            raise RuntimeError("Only NWU coordinate systems are supported")

        altitude_reference = get_altitude_reference_from_show_specification(show)
        light_program = get_light_program_from_show_specification(show)
        trajectory = get_trajectory_from_show_specification(show)
        geofence = get_geofence_configuration_from_show_specification(show)

        pyro_program = None
        rth_plan = None
        yaw_setpoints = None
        pro_keys = set(show.keys()).intersection(["pyro", "rthPlan", "yawControl"])
        if pro_keys:
            try:
                api = self.driver.app.import_api("show_pro")
                if not api.loaded:
                    raise RuntimeError(
                        f"Show pro extension is not loaded, neglecting {'and'.join(pro_keys)} from the show"
                    )
            except RuntimeError as ex:
                self.driver.log.warning(str(ex))
            else:
                pyro_program = api.encode_pyro(show)
                rth_plan = api.encode_rth_plan(show)
                yaw_setpoints = api.encode_yaw(show)

        async with SkybrushBinaryShowFile.create_in_memory() as show_file:
            await show_file.add_trajectory(trajectory)
            await show_file.add_encoded_light_program(light_program)
            if pyro_program:
                await show_file.add_encoded_event_list(pyro_program)
            if rth_plan:
                await show_file.add_encoded_rth_plan(rth_plan)
            if yaw_setpoints:
                await show_file.add_encoded_yaw_setpoints(yaw_setpoints)
            await show_file.finalize()
            data = show_file.get_contents()

        # Upload show file
        async with aclosing(MAVFTP.for_uav(self)) as ftp:
            await ftp.put(data, "/collmot/show.skyb")

        # We give some time for the filesystem to flush caches etc before
        # asking the drone to reload the show file. There were some reports
        # that sometimes the show file was read only partially, and I suspect
        # this could have been because the filesystem was not flushed fully
        # to the SD card before we tried to reload the show. We could not debug
        # it properly as it happened very rarely.

        # Encode latitude and longitude of show origin
        # TODO(ntamas): this is not entirely accurate due to the back-and-forth
        # conversion happening between floats and ints; sometimes the 7th
        # decimal digit is off by one.
        encoded_lat = int(coordinate_system.origin.lat * 1e7)
        encoded_lon = int(coordinate_system.origin.lon * 1e7)
        encoded_amsl = (
            int(altitude_reference * 1e3)
            if altitude_reference is not None
            else -32768000
        )

        # Try configuring with a single USER_2 command first, falling back to
        # the old parameter-based configuration if USER_2 is not supported.
        try:
            success = await self.driver.send_command_int(
                self,
                MAVCommand.USER_2,
                0,  # command code
                0,  # unused
                0,  # unused
                coordinate_system.orientation,
                encoded_lat,
                encoded_lon,
                encoded_amsl,
            )
        except NotSupportedError:
            success = False

        if not success:
            # Configure show origin, orientation and altitude reference using
            # the old method. There is no version of the firmware that would
            # support SHOW_ORIGIN_AMSL but does not support the new-style
            # configuration method so we raise an error if the user tries to
            # set an AMSL value
            if altitude_reference is not None:
                raise NotSupportedError(
                    "AMSL-based control is not supported in this firmware"
                )

            orientation = coordinate_system.orientation % 360
            await self.set_parameter("SHOW_ORIGIN_LAT", encoded_lat)
            await self.set_parameter("SHOW_ORIGIN_LNG", encoded_lon)
            await self.set_parameter("SHOW_ORIENTATION", orientation)

        # Configure and enable geofence
        await self.configure_geofence(geofence)

        # Ask drone to reload show file now that we are done with everything
        # else
        await self.reload_show()

    async def wait_until_connected(self) -> None:
        """Waits until the UAV becomes connected (i.e. when we see the next
        heartbeat message from the drone).

        Returns immediately if the drone is currently considered connected.
        """
        if self._connection_state is not ConnectionState.CONNECTED:
            await self._connected_event.wait()

    def _configure_data_streams_soon(self, force: bool = False) -> None:
        """Schedules a call to configure the data streams that we want to receive
        from the UAV, as soon as possible.

        Parameters:
            force: when `False` and a configuration request has been scheduled
                recently, the call will be ignored. When `True`, repeated attempts
                to configure the data streams will all be processed.
        """
        if not force and self._last_data_stream_configuration_attempted_at:
            now = monotonic()
            if now - self._last_data_stream_configuration_attempted_at < 5:
                return

        self.driver.run_in_background(self._configure_data_streams)
        self._last_data_stream_configuration_attempted_at = monotonic()

    async def _configure_data_streams(self) -> None:
        """Configures the data streams that we want to receive from the UAV."""
        success = False

        # We give ourselves 60 seconds to configure everything. Most of the
        # internal functions time out on their own anyway
        with move_on_after(60):
            self._configuring_data_streams = True
            try:
                await self._configure_data_streams_with_fine_grained_commands()
                success = True
            except NotSupportedError:
                await self._configure_data_streams_with_legacy_commands()
                success = True
            except TooSlowError:
                # attempt timed out, even after retries, so we just give up
                pass
            except FutureCancelled:
                # This is okay, server is shutting down
                return
            finally:
                self._configuring_data_streams = False

        # In case of a failure, we only print a warning here. Soon the GCS will
        # realize again that it is not receiving status updates from the drone
        # and will attempt to configure again.
        if not success:
            self.driver.log.warning(
                "Failed to configure data stream rates, trying again later",
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
            success = await self.driver.send_command_long(
                self,
                MAVCommand.SET_MESSAGE_INTERVAL,
                param1=message_id,
                param2=1000000 / interval_hz,
            )

            if not success:
                self.driver.log.warning(
                    f"Failed to configure data stream rate for message {message_id}",
                    extra={"id": log_id_for_uav(self)},
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

        if self.driver.mandatory_custom_mode is None:
            return

        try:
            await self.set_mode(self.driver.mandatory_custom_mode)
        except TooSlowError:
            self.driver.log.warning(
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
        if not self.driver.assume_data_streams_configured:
            self._configure_data_streams_soon(force=True)

        # No need to request the autopilot capabilities here; we do it after
        # every heartbeat if we don't have them yet. See the comment in
        # `self._request_autopilot_capabilities()` for an explanation.

        if self.driver.mandatory_custom_mode is not None:
            # Don't set the mode immediately because the drone might not
            # respond right after bootup
            self.driver.run_in_background(self._configure_mandatory_custom_mode)

        # Reset our internal state object of the compass calibration procedure
        self.compass_calibration.reset()

    def _clear_autopilot_capabilities(self) -> None:
        """Clears the cached autopilot capabilities and firmware version number
        of the UAV.

        This function should be called after a firmware update to ensure that
        we query the new firmware version after the update.
        """
        self._last_messages.pop(MAVMessageType.AUTOPILOT_VERSION, None)

    async def _request_autopilot_capabilities(self) -> None:
        """Sends a request to the autopilot to send its capabilities via MAVLink
        in a separate packet.
        """
        try:
            success = await self.driver.send_command_long(
                self, MAVCommand.REQUEST_AUTOPILOT_CAPABILITIES, param1=1
            )
        except FutureCancelled:
            # This is okay, server is shutting down
            return
        except TooSlowError:
            self.driver.log.warning(
                "Failed to request autopilot capabilities; no confirmation "
                "received in time",
                extra={"id": log_id_for_uav(self)},
            )
            return

        if not success:
            self.driver.log.warning(
                "UAV rejected to send autopilot capabilities",
                extra={"id": log_id_for_uav(self)},
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

    def _set_connection_state(
        self, value: ConnectionState, heartbeat: Optional[MAVLinkMessage]
    ) -> None:
        if self._connection_state is value:
            return

        self._connection_state = value

        if value is ConnectionState.DISCONNECTED:
            # Revert to the lowest MAVLink version that we support in case the UAV
            # was somehow reset and it does not "understand" MAVLink v2 in its new
            # configuration
            self._reset_mavlink_version()

        elif value is ConnectionState.CONNECTED:
            # We assume that the autopilot type stays the same even if we lost
            # contact with the drone or if it was rebooted. However, if we do not
            # know the autopilot type yet, we create an instance based on the
            # heartbeat
            if isinstance(self._autopilot, UnknownAutopilot) and heartbeat is not None:
                autopilot_cls = Autopilot.from_heartbeat(heartbeat)
                self._autopilot = autopilot_cls()

            if self._was_probably_rebooted_after_reconnection():
                if not self._first_connection:
                    self.driver.log.warning(
                        f"UAV {self.id} might have been rebooted; reconfiguring"
                    )

                self._first_connection = False
                self._handle_reboot()

            # Send "connected" event to listeners
            event = self._connected_event
            self._connected_event = Event()
            event.set()

    def _store_message(self, message: MAVLinkMessage) -> None:
        """Stores the given MAVLink message in the dictionary that maps
        MAVLink message types to their most recent versions that were seen
        for this UAV.
        """
        self._last_messages[message.get_msgId()].update(message)

    def _update_errors_from_drone_show_status_packet(self, status: DroneShowStatus):
        """Updates the error codes based on the most recent drone show status
        message.

        The error codes managed by this function are disjoint from the error
        codes managed by `_update_errors_from_sys_status_and_heartbeat()`,
        except for FlockwaveErrorCode.GEOFENCE_VIOLATION_WARNING, which is
        handled in `_update_errors_from_sys_status_and_heartbeat()`. See a
        detailed explanation in the source code there.
        """
        errors: dict[int, bool] = {
            FlockwaveErrorCode.TIMESYNC_ERROR.value: status.has_timesync_error,
            FlockwaveErrorCode.FAR_FROM_TAKEOFF_POSITION.value: status.is_misplaced_before_takeoff,
        }
        self.ensure_errors(errors)

    def _update_errors_from_inactive_state(self) -> None:
        """Updates the error codes in the case when we received a heartbeat
        message that seems to indicate that the drone cannot communicate now.

        Either this function or `_update_errors_from_sys_status_and_heartbeat()`
        will be called for a heartbeat packet but not both. The policy is that
        we clear error codes with a severity less than a "real" error and then
        mark the drone as "sleeping". This way we won't lose genuine error
        codes that happened while the drone was still awake.
        """
        self.clear_errors_up_to_and_including(FlockwaveErrorCode.UNSPECIFIED_WARNING)
        errors = {
            FlockwaveErrorCode.AUTOPILOT_INIT_FAILED.value: False,
            FlockwaveErrorCode.SLEEPING.value: True,
        }
        self.ensure_errors(errors)

    def _update_errors_from_sys_status_and_heartbeat(self) -> None:
        """Updates the error codes based on the most recent HEARTBEAT and
        SYS_STATUS messages. We need both to have an accurate picture of what is
        going on, hence a separate function that is called from both message
        handlers.

        The error codes managed by this function are disjoint from the error
        codes managed by `_update_errors_from_drone_show_status_packet()`.
        Make sure to keep it this way.

        This function does _not_ update the timestamp of the status information;
        you need to do it on your own by calling `update_status()` after calling
        this function.
        """
        # This function is called frequently, and Python enums are a bit slow
        # so we optimize enum access by using the 'value' property on them

        heartbeat = self.get_last_message(MAVMessageType.HEARTBEAT)
        sys_status = self.get_last_message(MAVMessageType.SYS_STATUS)
        if not heartbeat or not sys_status:
            return

        # Check error conditions from SYS_STATUS
        sensor_mask: int = (
            sys_status.onboard_control_sensors_enabled
            & sys_status.onboard_control_sensors_present
        )
        not_healthy_sensors: int = sensor_mask & (
            # Python has no proper bitwise negation on unsigned integers
            # so we use XOR instead
            sys_status.onboard_control_sensors_health ^ 0xFFFFFFFF
        )

        has_gyro_error = not_healthy_sensors & (
            MAVSysStatusSensor.GYRO_3D.value | MAVSysStatusSensor.GYRO2_3D.value
        )
        has_mag_error = not_healthy_sensors & (
            MAVSysStatusSensor.MAG_3D.value | MAVSysStatusSensor.MAG2_3D.value
        )
        has_accel_error = not_healthy_sensors & (
            MAVSysStatusSensor.ACCEL_3D.value | MAVSysStatusSensor.ACCEL2_3D.value
        )
        has_baro_error = not_healthy_sensors & (
            MAVSysStatusSensor.ABSOLUTE_PRESSURE.value
            | MAVSysStatusSensor.DIFFERENTIAL_PRESSURE.value
        )
        has_gps_error = not_healthy_sensors & MAVSysStatusSensor.GPS.value
        has_proximity_error = not_healthy_sensors & MAVSysStatusSensor.PROXIMITY.value
        has_motor_error = not_healthy_sensors & (
            MAVSysStatusSensor.MOTOR_OUTPUTS.value
            | MAVSysStatusSensor.REVERSE_MOTOR.value
        )
        has_geofence_error = not_healthy_sensors & MAVSysStatusSensor.GEOFENCE.value
        has_rc_error = not_healthy_sensors & MAVSysStatusSensor.RC_RECEIVER.value
        has_battery_error = not_healthy_sensors & MAVSysStatusSensor.BATTERY.value
        has_logging_error = not_healthy_sensors & MAVSysStatusSensor.LOGGING.value

        are_motor_outputs_disabled = self._autopilot.are_motor_outputs_disabled(
            heartbeat, sys_status
        )
        are_motors_running = heartbeat.base_mode & MAVModeFlag.SAFETY_ARMED.value
        is_prearm_check_in_progress = self._autopilot.is_prearm_check_in_progress(
            heartbeat, sys_status
        )
        is_returning_home = self._autopilot.is_rth_flight_mode(
            heartbeat.base_mode, heartbeat.custom_mode
        )
        is_in_standby = heartbeat.system_status == MAVState.STANDBY.value

        # The geofence status is a bit of a mess. ArduCopter and PX4 both report
        # geofence violations by marking the geofence sensor as "present,
        # enabled but not healthy". However, if the geofence action is set to
        # "report only" in ArduCopter, it does _not_ consider the geofence
        # sensor as enabled and we never get notifications about geofence
        # breaches (apart from the "Fence breached" STATUSTEXT message, which
        # we ignore because there is no corresponding "Breach resolved" message
        # so we don't know for how long we should mark the geofence breached).
        # That's why our Skybrush-specific status packet contains a bit to
        # indicate geofence breaches even if the geofence mode is set to
        # "report only" -- but it means that we have _two_ sources to check to
        # determine the error code to use for the geofence.

        show_stage = (
            self._last_skybrush_status_info.stage
            if self._last_skybrush_status_info
            else DroneShowExecutionStage.UNKNOWN
        )

        # We do not use the LANDED error code yet because the current versions
        # of the Skybrush firmware report "LANDED" for a long time after landing,
        # which means that we would get an all-blue display in Live after a
        # successful show.

        errors = {
            FlockwaveErrorCode.SLEEPING.value: False,
            FlockwaveErrorCode.LANDING.value: show_stage
            is DroneShowExecutionStage.LANDING,
            FlockwaveErrorCode.TAKEOFF.value: show_stage
            is DroneShowExecutionStage.TAKEOFF,
            FlockwaveErrorCode.AUTOPILOT_INIT_FAILED.value: (
                heartbeat.system_status == MAVState.UNINIT.value
            ),
            FlockwaveErrorCode.AUTOPILOT_INITIALIZING.value: (
                heartbeat.system_status == MAVState.BOOT.value
            ),
            FlockwaveErrorCode.UNSPECIFIED_ERROR.value: (
                # RC errors apparently trigger this error condition with
                # ArduCopter if we don't exclude it explicitly
                heartbeat.system_status == MAVState.CRITICAL.value
                and not not_healthy_sensors
                and not has_rc_error
            ),
            FlockwaveErrorCode.UNSPECIFIED_CRITICAL_ERROR.value: (
                heartbeat.system_status == MAVState.EMERGENCY.value
                and not not_healthy_sensors
            ),
            FlockwaveErrorCode.MAGNETIC_ERROR.value: has_mag_error,
            FlockwaveErrorCode.GYROSCOPE_ERROR.value: has_gyro_error,
            FlockwaveErrorCode.ACCELEROMETER_ERROR.value: has_accel_error,
            FlockwaveErrorCode.PRESSURE_SENSOR_ERROR.value: has_baro_error,
            FlockwaveErrorCode.GPS_SIGNAL_LOST.value: has_gps_error,
            FlockwaveErrorCode.PROXIMITY_ERROR.value: has_proximity_error,
            FlockwaveErrorCode.MOTOR_MALFUNCTION.value: has_motor_error,
            FlockwaveErrorCode.GEOFENCE_VIOLATION.value: (
                has_geofence_error and are_motors_running
            ),
            FlockwaveErrorCode.GEOFENCE_VIOLATION_WARNING.value: (
                # Geofence error reported from SYS_STATUS...
                (has_geofence_error and not are_motors_running)
                # ...or no error reported from SYS_STATUS, but a geofence breach
                # was reported in the Skybrush-specific status packet
                or (
                    not has_geofence_error
                    and self._last_skybrush_status_info
                    and self._last_skybrush_status_info.is_geofence_breached
                )
            ),
            FlockwaveErrorCode.DRIFT_FROM_DESIRED_POSITION.value: (
                self._last_skybrush_status_info
                and self._last_skybrush_status_info.is_far_from_expected_position
            ),
            FlockwaveErrorCode.RC_SIGNAL_LOST_WARNING.value: has_rc_error,
            FlockwaveErrorCode.BATTERY_CRITICAL.value: has_battery_error,
            FlockwaveErrorCode.LOGGING_DEACTIVATED.value: has_logging_error,
            FlockwaveErrorCode.DISARMED.value: are_motor_outputs_disabled,
            FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS.value: is_prearm_check_in_progress,
            # If the motors are not running yet but we are on the ground, ready
            # to fly, we use an informational flag to let the user know
            FlockwaveErrorCode.ON_GROUND.value: not are_motors_running
            and is_in_standby,
            # If the motors are running but we are not in the air yet; we use an
            # informational flag to let the user know
            FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND.value: are_motors_running
            and is_in_standby,
            # Use the special RTH error code if the drone is in RTH or smart RTH mode
            # and its mode index is larger than the standby mode (typically:
            # active, critical, emergency, poweroff, termination)
            FlockwaveErrorCode.RETURN_TO_HOME.value: is_returning_home
            and heartbeat.system_status > MAVState.STANDBY.value,
        }

        # Clear the collected prearm failure messages if the heartbeat and/or
        # the system status shows that we are not in the prearm check phase any
        # more
        if not is_prearm_check_in_progress:
            self._preflight_status.message = "Passed"
            self._preflight_status.result = PreflightCheckResult.PASS

        # Update the error flags as needed
        self.ensure_errors(errors)

    def _update_gps_fix_type_and_satellite_count(
        self, type: OurGPSFixType, num_satellites: Optional[int]
    ) -> None:
        """Updates the GPS fix and the number of satellites in the internal
        GPSFix_ object that is then later used to update the status object of
        the UAV.
        """
        self._gps_fix.type = type
        self._gps_fix.num_satellites = num_satellites

    def _was_probably_rebooted_after_reconnection(self) -> bool:
        """Returns whether the UAV was probably rebooted recently, _assuming_
        that a reconnection event happened.

        This function _must_ be called only after a reconnection event. Right
        now we always return `False`, but we could implement a more sophisticated
        check in the future based on the `SYSTEM_TIME` messages and whether the
        `time_boot_ms` timestamp has decreased.
        """
        return False
