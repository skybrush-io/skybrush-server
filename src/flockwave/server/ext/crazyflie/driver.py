"""Driver class for Crazyflie drones."""

from __future__ import annotations

from collections import defaultdict
from colour import Color
from contextlib import asynccontextmanager, AsyncExitStack
from errno import EIO
from functools import partial
from logging import Logger
from math import ceil, hypot
from pathlib import Path
from random import random
from struct import Struct
from trio import Nursery, open_nursery, sleep
from trio_util import periodic
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Optional,
    Sequence,
    Tuple,
    TYPE_CHECKING,
    cast,
)

from aiocflib.crazyflie import Crazyflie
from aiocflib.crtp.crtpstack import MemoryType
from aiocflib.crazyflie.high_level_commander import TrajectoryType
from aiocflib.crazyflie.log import LogSession
from aiocflib.crazyflie.mem import write_with_checksum
from aiocflib.errors import TimeoutError

from flockwave.gps.vectors import PositionXYZ, VelocityXYZ
from flockwave.server.command_handlers import (
    create_color_command_handler,
    create_parameter_command_handler,
    create_version_command_handler,
)
from flockwave.server.errors import NotSupportedError
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.transport import TransportOptions
from flockwave.server.model.uav import BatteryInfo, UAVBase, UAVDriver, VersionInfo
from flockwave.server.registries.errors import RegistryFull
from flockwave.server.utils import color_to_rgb8_triplet, nop, optional_float
from flockwave.spec.errors import FlockwaveErrorCode
from flockwave.spec.ids import make_valid_object_id

from skybrush import (
    get_group_index_from_show_specification,
    get_home_position_from_show_specification,
    get_light_program_from_show_specification,
    get_trajectory_from_show_specification,
    TrajectorySpecification,
)

from .crtp_extensions import (
    DRONE_SHOW_PORT,
    DroneShowCommand,
    DroneShowExecutionStage,
    DroneShowStatus,
    GCSLightEffectType,
    LightProgramLocation,
    LightProgramType,
    PreflightCheckStatus,
)
from .fence import Fence, FenceConfiguration
from .trajectory import encode_trajectory, TrajectoryEncoding

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

__all__ = ("CrazyflieDriver",)


class CrazyflieDriver(UAVDriver):
    """Driver class for Crazyflie drones.

    Attributes:
        app (SkybrushServer): the app in which the driver lives
        fence_config: configuration of the safety fence to apply on drones
            managed by this driver
        id_format: Python format string that receives a numeric drone ID in the
            flock and returns its preferred formatted identifier that is used
            when the drone is registered in the server, or any other object that
            has a ``format()`` method accepting a single integer as an argument
            and returning the preferred UAV identifier
        status_interval: number of seconds that should pass between consecutive
            status requests sent to a drone
        use_fake_position: whether to feed a fake position into the positioning
            system of the connected drones, strictly for testing purposes
    """

    app: "SkybrushServer"
    debug: bool
    id_format: str
    log: Logger
    fence_config: FenceConfiguration
    status_interval: float = 0.5
    use_fake_position: Optional[Tuple[float, float, float]] = None
    use_test_mode: bool = False

    def __init__(
        self,
        app=None,
        id_format: str = "{0:02}",
        cache: Optional[Path] = None,
    ):
        """Constructor.

        Parameters:
            app (SkybrushServer): the app in which the driver lives
            debug (bool): whether to log the incoming and outgoing messages of
                each drone created by the driver
            id_format: the format of the UAV IDs used by this driver.
                See the class documentation for more details.
            cache: optional cache folder that the driver can use to store the
                parameter and log TOCs of the Crazyflie drones that it encounters
        """
        super().__init__()

        self.app = app
        self.debug = False
        self.fence_config = FenceConfiguration()
        self.id_format = id_format
        self.use_fake_position = None
        self.use_test_mode = False

        self._cache_folder = str(cache.resolve()) if cache else None
        self._uav_ids_by_address_space = defaultdict(dict)

    def _create_uav(self, formatted_id: str) -> "CrazyflieUAV":
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            formatted_id: the formatted string identifier of the UAV
                to create

        Returns:
            an appropriate UAV object
        """
        uav = CrazyflieUAV(formatted_id, driver=self)
        uav.notify_updated = partial(
            self.app.request_to_send_UAV_INF_message_for, [formatted_id]
        )
        uav.send_log_message_to_gcs = partial(
            self.app.request_to_send_SYS_MSG_message, sender=uav.id
        )
        return uav

    @property
    def cache_folder(self) -> Optional[str]:
        """Returns the full path to a folder where the driver can store
        the parameter TOC files of the Crazyflie drones that it sees.
        """
        return self._cache_folder

    def get_or_create_uav(self, address_space, index: int) -> Optional["CrazyflieUAV"]:
        """Retrieves the UAV with the given index in the given address space
        or creates one if the driver has not seen a UAV with the given index in
        the given address space yet.

        Parameters:
            address_space: the address space
            index: the index of the address within the address space

        Returns:
            an appropriate UAV object or `None` if the UAV object cannot be
            added to the object registry due to the registry being full
        """
        uav_id_map = self._uav_ids_by_address_space.get(address_space)
        formatted_id = uav_id_map.get(index) if uav_id_map else None
        if formatted_id is None:
            formatted_id = make_valid_object_id(
                self.id_format.format(index, address_space)
            )
            self._uav_ids_by_address_space[address_space][index] = formatted_id

        try:
            uav = cast(
                Any,
                self.app.object_registry.add_if_missing(
                    formatted_id, factory=self._create_uav
                ),
            )
        except RegistryFull:
            return None

        if uav.uri is None:
            uav.uri = address_space[index]

        return uav

    async def handle_command_alt(
        self,
        uav: "CrazyflieUAV",
        z: Optional[str] = None,
    ):
        """Command that sends the UAV to a given altitude."""
        try:
            z_num = optional_float(z)
        except ValueError:
            raise RuntimeError("Invalid number found in input")

        if z_num is None:
            current = uav.status.position_xyz
            if current is None:
                raise RuntimeError("UAV has no known position yet")
            return f"Current altitude is {current.z:.2f} m"
        else:
            x_num, y_num, z_num = await uav.go_to(None, None, z_num)
            return f"Target set to ({x_num:.2f}, {y_num:.2f}, {z_num:.2f}) m"

    async def handle_command_fence(
        self,
        uav: "CrazyflieUAV",
        subcommand: Optional[str] = None,
        x_min: Optional[str] = None,
        y_min: Optional[str] = None,
        z_min: Optional[str] = None,
        x_max: Optional[str] = None,
        y_max: Optional[str] = None,
        z_max: Optional[str] = None,
    ):
        """Command that activates or deactivates the geofence on the UAV, or
        sets up a geofence based on an axis-aligned bounding box.
        """
        if subcommand is None:
            subcommand = "get"

        subcommand = subcommand.lower()
        fence = uav.fence

        if fence is None:
            raise RuntimeError("Drone has no fence object; this is probably a bug.")

        if subcommand == "get":
            enabled = await fence.is_enabled()
            return "Fence is active" if enabled else "Fence is disabled"
        elif subcommand == "on":
            await fence.set_enabled(True)
            return "Fence activated"
        elif subcommand == "off":
            await fence.set_enabled(False)
            return "Fence disabled"
        elif subcommand == "set":
            try:
                bounds = [float(x) for x in (x_min, y_min, z_min, x_max, y_max, z_max)]  # type: ignore
            except (TypeError, ValueError):
                raise RuntimeError(
                    "Invalid fence coordinates; expected xMin, yMin, zMin, xMax, yMax, zMax, separated by spaces"
                )

            await fence.set_axis_aligned_bounding_box(bounds[:3], bounds[3:])
            return "Fence activated"
        else:
            raise RuntimeError(f"Unknown subcommand: {subcommand}")

    async def handle_command_go(
        self,
        uav: "CrazyflieUAV",
        x: Optional[str] = None,
        y: Optional[str] = None,
        z: Optional[str] = None,
    ):
        """Command that sends the UAV to a given coordinate."""
        if x is None and y is None and z is None:
            raise RuntimeError(
                "You need to specify the target coordinate in X-Y-Z format"
            )

        try:
            coords = optional_float(x), optional_float(y), optional_float(z)
        except ValueError:
            raise RuntimeError("Invalid number found in input")

        x_num, y_num, z_num = coords
        x_num, y_num, z_num = await uav.go_to(x_num, y_num, z_num)
        return f"Target set to ({x_num:.2f}, {y_num:.2f}, {z_num:.2f}) m"

    async def handle_command_home(self, uav: "CrazyflieUAV"):
        """Command that retrieves the current home position of the UAV."""
        home = await uav.get_home_position()
        if home is None:
            return "No home position yet"
        else:
            x, y, z = home
            return f"Home: [{x:.2f}, {y:.2f}, {z:.2f}] m"

    async def handle_command_kalman(
        self, uav: "CrazyflieUAV", command: Optional[str] = None
    ) -> str:
        if command is None:
            return "Run 'kalman reset' to reset the Kalman filter"
        elif command == "reset":
            await uav.set_parameter("kalman.resetEstimation", 1)
            return "Kalman filter reset successfully"
        else:
            raise RuntimeError(f"Unknown command: {command}")

    async def handle_command_land(self, uav: "CrazyflieUAV") -> str:
        await uav.land()
        return "Land command sent successfully"

    async def handle_command_rush(
        self,
        uav: "CrazyflieUAV",
        x: Optional[str] = None,
        y: Optional[str] = None,
        z: Optional[str] = None,
        multiplier: Optional[str] = None,
    ):
        """Temporary command for Nina's project that sends the UAV to a given
        coordinate _very_ quickly."""
        if x is None and y is None and z is None:
            raise RuntimeError(
                "You need to specify the target coordinate in X-Y-Z format"
            )

        try:
            coords = optional_float(x), optional_float(y), optional_float(z)
            multiplier_num = optional_float(multiplier)
        except ValueError:
            raise RuntimeError("Invalid number found in input")

        if multiplier_num is None:
            multiplier_num = 1

        x_num, y_num, z_num = coords
        velocity_xy, velocity_z = 4, 1
        velocity_xy *= multiplier_num
        velocity_z *= multiplier_num
        x_num, y_num, z_num = await uav.go_to(
            x_num, y_num, z_num, velocity_xy, velocity_z, min_travel_time=0.2
        )

        return f"Target set to ({x_num:.2f}, {y_num:.2f}, {z_num:.2f}) m"

    async def handle_command_show(
        self, uav: "CrazyflieUAV", command: Optional[str] = None
    ) -> str:
        if command is None:
            return "Run 'show clear' to clear the last uploaded show"
        elif command == "clear":
            if uav.has_previously_uploaded_show:
                uav.forget_last_uploaded_show()
                await uav.reboot()
                return "Last uploaded show cleared, drone rebooted."
            else:
                return "No show was recently uploaded to this drone."
        else:
            raise RuntimeError(f"Unknown command: {command}, expected 'clear'")

    async def handle_command_stop(self, uav: "CrazyflieUAV") -> str:
        """Stops the motors of the UAV immediately."""
        await uav.stop()
        return "Motor stop signal sent"

    async def handle_command_test(
        self, uav: "CrazyflieUAV", component: Optional[str] = None
    ) -> str:
        """Runs a self-test on a component of the UAV."""
        if component == "motor":
            # TODO(ntamas): allow this only when the drone is on the ground!
            await uav.test_component("motor")
            return "Motor test started"
        elif component == "battery":
            # TODO(ntamas): allow this only when the drone is on the ground!
            await uav.test_component("battery")
            return "Battery test started"
        elif component == "led":
            await uav.test_component("led")
            return "LED test executed"
        else:
            return "Usage: test <battery|led|motor>"

    async def handle_command_trick(self, uav: "CrazyflieUAV", *params: str) -> str:
        """Non-public command used for Nina's show as a last resort hack to send
        the drone through the ceiling if the high-level commander is not suitable
        for the task.
        """
        z_velocity = 1
        distance = 3
        hover_time = 5

        if len(params) > 0:
            z_velocity = max(0.2, float(params[0]))
        if len(params) > 1:
            distance = max(0.2, float(params[1]))
        if len(params) > 2:
            hover_time = float(params[2])

        # Nina's shoot-the-drone-through-the-ceiling trick
        await uav.perform_nina_trick(
            z_velocity=z_velocity, distance=distance, hover_time=hover_time
        )

        return f"Velocity = {z_velocity} m/s, distance = {distance} m, hover time = {hover_time} s"

    async def handle_command___show_upload(self, uav: "CrazyflieUAV", *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        await uav.upload_show(show, remember=True)

    handle_command_color = create_color_command_handler()
    handle_command_motoroff = handle_command_stop
    handle_command_param = create_parameter_command_handler()
    handle_command_version = create_version_command_handler()

    async def _enter_low_power_mode_single(
        self, uav: "CrazyflieUAV", *, transport: Optional[TransportOptions]
    ) -> None:
        await uav.enter_low_power_mode()

    async def _resume_from_low_power_mode_single(
        self, uav: "CrazyflieUAV", *, transport: Optional[TransportOptions]
    ) -> None:
        await uav.resume_from_low_power_mode()

    def _request_preflight_report_single(
        self, uav: "CrazyflieUAV"
    ) -> PreflightCheckInfo:
        return uav.preflight_status

    async def _request_version_info_single(self, uav: "CrazyflieUAV") -> VersionInfo:
        return await uav.get_version_info()

    async def _send_landing_signal_single(
        self, uav: "CrazyflieUAV", *, transport: Optional[TransportOptions]
    ) -> None:
        if uav.is_in_drone_show_mode:
            await uav.stop_drone_show()
        else:
            await uav.land()

    async def _send_light_or_sound_emission_signal_single(
        self,
        uav: "CrazyflieUAV",
        signals,
        duration,
        *,
        transport: Optional[TransportOptions],
    ) -> None:
        if "light" in signals:
            await uav.emit_light_signal()

    async def _send_motor_start_stop_signal_single(
        self,
        uav: "CrazyflieUAV",
        start: bool,
        force: bool,
        *,
        transport: Optional[TransportOptions],
    ) -> None:
        if start:
            await uav.arm(force=force)
        else:
            await uav.disarm(force=force)

    async def _send_reset_signal_single(
        self,
        uav: "CrazyflieUAV",
        component: str,
        *,
        transport: Optional[TransportOptions],
    ) -> None:
        if not component:
            # Resetting the whole UAV, this is supported
            # TODO(ntamas): log blocks have to be re-configured after reboot
            await uav.reboot()
        else:
            # No component resets are implemented on this UAV yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_shutdown_signal_single(
        self, uav: "CrazyflieUAV", *, transport: Optional[TransportOptions]
    ) -> None:
        await uav.shutdown()

    async def _send_takeoff_signal_single(
        self, uav, *, scheduled: bool = False, transport: Optional[TransportOptions]
    ) -> None:
        if scheduled:
            # Handled by a broadcast signal in the extension class
            return

        if uav.is_in_drone_show_mode:
            await uav.start_drone_show()
        else:
            await uav.takeoff(altitude=1, relative=True)


class CrazyflieUAV(UAVBase):
    """Subclass for UAVs created by the driver for Crazyflie drones.

    Attributes:
        uri: the Crazyflie URI of the drone
    """

    _preflight_result_map = [
        PreflightCheckResult.OFF,
        PreflightCheckResult.FAILURE,
        PreflightCheckResult.RUNNING,
        PreflightCheckResult.PASS,
        PreflightCheckResult.SOFT_FAILURE,
    ]

    driver: CrazyflieDriver
    notify_updated: Callable[[], None]
    notify_shutdown_suspend_or_reboot: Callable[[], None]
    send_log_message_to_gcs: Callable[[str], None]
    uri: Optional[str]

    _armed: bool
    _crazyflie: Optional[Crazyflie]
    _fence: Optional[Fence]
    _fence_breached: bool
    _log_session: Optional[LogSession]

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        self.uri = None
        self.notify_updated = nop
        self.notify_shutdown_suspend_or_reboot = nop
        self.send_log_message_to_gcs = nop

        self._crazyflie = None
        self._fence = None
        self._log_session = None
        self._last_uploaded_show = None

        self._reset_status_variables()

    def _get_crazyflie(self) -> Crazyflie:
        """Returns the internal Crazyflie_ object that represents the connection
        to the drone.

        Raises:
            RuntimeError: if the connection to the Crazyflie is not established
        """
        if self._crazyflie is None:
            raise RuntimeError(f"Not connected to the Crazyflie drone at {self.uri}")
        return self._crazyflie

    @property
    def fence(self) -> Optional[Fence]:
        return self._fence

    async def arm(self, force: bool = False) -> None:
        """Arms the motors of the Crazyflie."""
        await self._get_crazyflie().run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.ARM_OR_DISARM,
            data=Struct("<B").pack(3 if force else 1),
        )

    async def disarm(self, force: bool = False) -> None:
        """Disarms or force-disarms the motors of the Crazyflie."""
        await self._get_crazyflie().run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.ARM_OR_DISARM,
            data=Struct("<B").pack(2 if force else 0),
        )

    async def emit_light_signal(self) -> None:
        """Asks the UAV to emit a visible light signal from its LED ring to
        attract attention.
        """
        await self._get_crazyflie().led_ring.flash()

    async def enter_low_power_mode(self) -> None:
        """Sends the UAV to a low-power mode where only the radio chip is
        listening for incoming packets.
        """
        await self._get_crazyflie().suspend()
        self.notify_shutdown_suspend_or_reboot()

    def forget_last_uploaded_show(self) -> None:
        """Forgets the last uploaded show to this drone so it does not get
        re-uploaded if the drone is rebooted.
        """
        self._last_uploaded_show = None

    async def get_home_position(self) -> Optional[Tuple[float, float, float]]:
        """Returns the current home position of the UAV."""
        x = await self.get_parameter("preflight.homeX", fetch=True)
        y = await self.get_parameter("preflight.homeY", fetch=True)
        z = await self.get_parameter("preflight.homeZ", fetch=True)
        if not x and not y and z <= -10000:
            return None
        else:
            return x, y, z

    async def get_parameter(self, name: str, fetch: bool = False) -> float:
        """Returns the value of a parameter from the Crazyflie."""
        return await self._get_crazyflie().param.get(name, fetch=fetch)

    async def get_version_info(self) -> VersionInfo:
        cf = self._get_crazyflie()
        version = await cf.platform.get_firmware_version()
        revision = await cf.platform.get_firmware_revision()
        return {"firmware": version or "", "revision": revision or ""}

    async def go_to(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        velocity_xy: float = 2,
        velocity_z: float = 0.5,
        min_travel_time: float = 1,
    ) -> Tuple[float, float, float]:
        """Sends the UAV to a given coordinate.

        Parameters:
            x: the X coordinate of the target; ``None`` means to use the current
                X coordinate
            y: the Y coordinate of the target; ``None`` means to use the current
                Y coordinate
            z: the Z coordinate of the target; ``None`` means to use the current
                Z coordinate
            velocity_xy: maximum allowed horizontal velocity, in m/s
            velocity_z: maximum allowed vertical velocity, in m/s
            min_travel_time: minimum travel time; useful for very small changes
                in the target position
        """
        current = self.status.position_xyz
        if current is None:
            raise RuntimeError("UAV has no known position yet")

        cf = self._get_crazyflie()

        target_x = current.x if x is None else x
        target_y = current.y if y is None else y
        target_z = current.z if z is None else z

        dx, dy, dz = target_x - current.x, target_y - current.y, target_z - current.z
        travel_time = max(
            min_travel_time, hypot(dx, dy) / velocity_xy, abs(dz) / velocity_z
        )

        # TODO(ntamas): keep current yaw!
        await cf.high_level_commander.go_to(
            target_x, target_y, target_z, yaw=0, duration=travel_time
        )

        return target_x, target_y, target_z

    @property
    def has_previously_uploaded_show(self) -> bool:
        """Returns whether the UAV knows about a show that was already uploaded
        to it at least once, possibly during a previous boot.
        """
        return self._last_uploaded_show is not None

    @property
    def is_in_drone_show_mode(self) -> bool:
        """Returns whether the UAV is in drone show mode."""
        return self._status.mode == "show"

    @property
    def is_running_show(self) -> bool:
        """Returns whether the UAV is currently executing a show."""
        return not self._show_execution_stage.is_idle

    async def land(self, altitude: float = 0.0, velocity: float = 0.5):
        """Initiates a landing to the given altitude (absolute or relative).

        Parameters:
            altitude: the altitude to reach at the end of the landing operation,
                in meters
            velocity: the desired takeoff velocity, in meters per second
        """
        # TODO(ntamas): launch this in a separate background task and return
        # early with the result
        # TODO(ntamas): figure out how much time the landing will take
        # approximately and shut down the motors at the end
        await self._get_crazyflie().high_level_commander.land(
            altitude, velocity=velocity
        )

    @property
    def last_uploaded_show(self):
        """Reference to the last show data that was uploaded to this Crazyflie,
        even if it was rebooted in the meanwhile.
        """
        return self._last_uploaded_show

    @property
    def log_session(self) -> Optional[LogSession]:
        """Returns the logging session that the Crazyflie currently uses."""
        return self._log_session

    @property
    def preflight_status(self) -> PreflightCheckInfo:
        return self._preflight_status

    async def perform_nina_trick(
        self, z_velocity: float = 1, distance: float = 3, hover_time: float = 5
    ) -> None:
        """Hacky implementation of Nina's send-the-drone-through-the-ceiling
        trick.

        Parameters:
            z_velocity: vertical velocity of the drone during ascent, in
                meters per seocnd
            distance: distance to travel during ascent, in meters. The duration
                of the ascent will be derived from the Z velocity and the distance
            hover_time: hover time _after_ the ascent and _before_ the motors are
                turned off, in seconds
        """
        cf = self._get_crazyflie()
        dt = 0.1

        # Disable the high-level commander so the show subsystem knows that we
        # have intervened. Try this multiple times in case the connection is
        # flaky.
        tries = 5
        while True:
            try:
                await cf.high_level_commander.disable()
            except Exception:
                if tries > 0:
                    tries -= 1
                else:
                    raise
            else:
                break

        # Perform the upward ascent
        duration = distance / z_velocity
        iterations = int(ceil(duration / dt))
        for _ in range(iterations):
            try:
                await cf.commander.send_altitude_hold_setpoint(z_velocity=z_velocity)
            except Exception:
                pass
            await sleep(dt)

        # Wait and hover a bit until the trap door is closed
        iterations = int(ceil(hover_time / dt))
        for _ in range(iterations):
            try:
                await cf.commander.send_altitude_hold_setpoint(z_velocity=0)
            except Exception:
                pass
            await sleep(dt)

        # Stop the motors and prevent them from starting again by pretending
        # that the Crazyflie has flipped
        tries = 20
        while True:
            try:
                await cf.commander.send_stop_setpoint()
                await cf.param.set("stabilizer.stop", 1)
            except Exception:
                if tries > 0:
                    tries -= 1
                else:
                    raise
            else:
                break

    async def process_console_messages(self) -> None:
        """Runs a task that processes incoming console messages and forwards
        them to the logger of the extension.
        """
        extra = {"id": self.id}
        console = self._get_crazyflie().console
        async for message in console.messages():
            self.driver.log.info(message, extra=extra)
            self.send_log_message_to_gcs(message)

    async def process_drone_show_status_messages(self, period: float = 0.5) -> None:
        """Runs a task that requests a drone show related status report from
        the Crazyflie drone repeatedly.

        Parameters:
            period: the number of seconds elapsed between consecutive status
                requests, in seconds
        """
        await sleep(random() * period)
        async for _ in periodic(period):
            cf = self._get_crazyflie()
            try:
                status = await cf.run_command(
                    port=DRONE_SHOW_PORT, command=DroneShowCommand.STATUS
                )
                status = DroneShowStatus.from_bytes(status)
            except TimeoutError:
                status = None
            except Exception:
                self.driver.log.warn(
                    "Malformed drone show status packet received, ignoring"
                )
                status = None

            if status:
                message = status.show_execution_stage.get_short_explanation()
                if status.testing:
                    message = f"(test) {message}"
                message = message.encode("utf-8")

                self._armed = status.armed
                self._battery.charging = status.charging
                self._battery.voltage = status.battery_voltage
                self._battery.percentage = status.battery_percentage
                self._fence_breached = status.fence_breached
                self._position.update(
                    x=status.position[0], y=status.position[1], z=status.position[2]
                )
                self._show_execution_stage = status.show_execution_stage
                self._update_preflight_status_from_result_codes(status.preflight_checks)
                self._update_error_codes()
                self.update_status(
                    battery=self._battery,
                    mode=status.mode,
                    light=status.light,
                    position_xyz=self._position,
                    debug=message,
                    heading=status.yaw,
                )
                self.notify_updated()

    async def process_log_messages(self) -> None:
        """Runs a task that processes incoming log messages and calls the
        appropriate log message handlers.
        """
        assert self._log_session is not None
        await self._log_session.process_messages()

    async def reboot(self):
        """Reboots the UAV."""
        await self._get_crazyflie().reboot()
        self.notify_shutdown_suspend_or_reboot()

    async def resume_from_low_power_mode(self) -> None:
        """Wakes up the UAV if it has been sent to a low-power mode earlier."""
        await self._get_crazyflie().resume()

    async def reupload_last_show(self) -> None:
        """Uploads the last show that was uploaded to this drone again."""
        if self.last_uploaded_show:
            await self.upload_show(self.last_uploaded_show, remember=True)

    async def run(self, disposer: Optional[Callable[[], None]] = None):
        """Starts the main message handler task of the UAV."""
        try:
            await CrazyflieHandlerTask(
                self,
                debug=self.driver.debug,
                log=self.driver.log,
                status_interval=self.driver.status_interval,
                use_fake_position=self.driver.use_fake_position,
            ).run()
        finally:
            if disposer:
                disposer()

    async def set_parameter(self, name: str, value: float) -> None:
        """Sets the value of a parameter on the Crazyflie."""
        await self._get_crazyflie().param.set(name, value)

    @asynccontextmanager
    async def set_and_restore_parameter(
        self, name: str, value: float
    ) -> AsyncIterator[None]:
        """Context manager that sets the value of a parameter on the UAV upon
        entering the context and resets it upon exiting.
        """
        async with self._get_crazyflie().param.set_and_restore(name, value):
            yield

    async def set_home_position(
        self, pos: Optional[Tuple[float, float, float]]
    ) -> None:
        """Sets or clears the home position of the UAV.

        Parameters:
            pos: the home position of the UAV, in the local coordinate system.
                Units are in meters. `None` means to clear the home position.
        """
        x, y, z = (0, 0, -10000) if pos is None else pos
        await self.set_parameter("preflight.homeX", x)
        await self.set_parameter("preflight.homeY", y)
        await self.set_parameter("preflight.homeZ", z)

    async def set_led_color(self, color: Optional[Color] = None):
        """Overrides the color of the LED ring of the UAV.

        Parameters:
            color: the color to apply; `None` turns off the override.
        """
        if color is not None:
            red, green, blue = color_to_rgb8_triplet(color)
        else:
            red, green, blue = 0, 0, 0

        await self._get_crazyflie().run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.TRIGGER_GCS_LIGHT_EFFECT,
            data=Struct("<BBBB").pack(
                GCSLightEffectType.SOLID
                if color
                else GCSLightEffectType.OFF,  # effect ID
                red,
                green,
                blue,
            ),
        )

    async def stop(self) -> None:
        """Stops the motors of the UAV immediately."""
        cf = self._get_crazyflie()
        await cf.commander.stop()
        await cf.high_level_commander.stop()

    async def shutdown(self) -> None:
        """Shuts down the UAV."""
        await self._get_crazyflie().shutdown()
        self.notify_shutdown_suspend_or_reboot()

    async def start_drone_show(self, delay: float = 0):
        """Instructs the UAV to start the pre-programmed drone show in drone
        show mode. Assumes that the UAV is already in drone show mode; it will
        _not_ attempt to switch the mode.
        """
        if delay > 0:
            delay = int(delay * 1000)
            if abs(delay) >= 32000:
                raise RuntimeError("Maximum allowed delay is 32 seconds")
            data = Struct("<h").pack(delay)
        else:
            data = None

        await self._get_crazyflie().run_command(
            port=DRONE_SHOW_PORT, command=DroneShowCommand.START, data=data
        )

    async def stop_drone_show(self):
        """Instructs the UAV to stop the pre-programmed drone show in drone
        show mode. Assumes that the UAV is already in drone show mode; it will
        _not_ attempt to switch the mode.
        """
        await self._get_crazyflie().run_command(
            port=DRONE_SHOW_PORT, command=DroneShowCommand.STOP
        )

    async def takeoff(
        self, altitude: float = 1.0, relative: bool = False, velocity: float = 0.5
    ):
        """Initiates a takeoff to the given altitude (absolute or relative).

        Parameters:
            altitude: the altitude to reach at the end of the takeoff operation,
                in meters
            relative: whether the altitude is relative to the current position
            velocity: the desired takeoff velocity, in meters per second
        """
        await self._get_crazyflie().high_level_commander.takeoff(
            altitude, relative=relative, velocity=velocity
        )

    async def test_component(self, component: str) -> None:
        """Tests a component of the UAV.

        Parameters:
            component: the component to test; currently we support ``motor``,
                ``battery`` and ``led``
        """
        if component == "motor":
            await self.set_parameter("health.startPropTest", 1)
        elif component == "led":
            await self._get_crazyflie().led_ring.test()
        elif component == "battery":
            await self.set_parameter("health.startBatTest", 1)
        else:
            raise NotSupportedError

    async def upload_show(self, show, *, remember: bool = True) -> None:
        home = get_home_position_from_show_specification(show)
        trajectory = get_trajectory_from_show_specification(show)
        group_index = get_group_index_from_show_specification(show)
        if group_index > 7:
            raise RuntimeError("Crazyflie drones support at most 8 groups only")

        scale = trajectory.propose_scaling_factor()
        if scale > 1:
            raise RuntimeError("Trajectory covers too large an area for a Crazyflie")

        light_program = get_light_program_from_show_specification(show)
        try:
            await self._upload_light_program(light_program)
        except OSError as ex:
            if ex.errno == EIO:
                raise RuntimeError(
                    "IO error while uploading light program; is it too large?"
                )
            else:
                raise

        try:
            await self._upload_trajectory_and_fence(
                trajectory, home, fence_config=self.driver.fence_config
            )
        except OSError as ex:
            if ex.errno == EIO:
                raise RuntimeError(
                    "IO error while uploading trajectory; is it too large?"
                )
            else:
                raise

        assert self._crazyflie is not None
        await self._crazyflie.high_level_commander.set_group_mask(1 << group_index)

        await self._enable_show_mode()

        self._last_uploaded_show = show if remember else None

    @asynccontextmanager
    async def use(self, debug: bool = False):
        """Async context manager that establishes a low-level connection to the
        drone given its URI when the context is entered, and closes the
        connection when the context is exited.

        Parameters:
            debug: whether to print the messages passed between the drone and
                the server to the console
        """
        uri = self.uri

        if uri is None:
            raise RuntimeError("Crazyflie URI is not set yet")

        if debug and "+log" not in uri:
            uri = uri.replace("://", "+log://")

        try:
            async with Crazyflie(
                uri, cache=self.driver.cache_folder
            ) as self._crazyflie:
                self._fence = Fence(self._crazyflie)
                await self._crazyflie.log.validate()
                try:
                    self._log_session = self._setup_logging_session()
                    yield self._crazyflie
                finally:
                    self._log_session = None
        finally:
            self._fence = None
            self._crazyflie = None

    async def setup_flight_mode(self):
        """Sets up the appropriate flight mode (high level controller or drone
        show mode). This function should be called after (re-)establishing
        connection with a Crazyflie.

        The rule is that we set the Crazyflie into drone show mode if a drone
        show has been uploaded to it at least once (even if it was with an
        earlier booting attempt), otherwise we simply turn on the high level
        commander only.

        Note that the function is _not_ a context manager, i.e. it does not
        restore the original flight mode when exiting the context. This is
        intentional -- we don't want to get the Crazyflie out of its current
        flight mode if we accidentally lose contact with it for a split second
        only.
        """
        needs_show_mode = self.has_previously_uploaded_show
        cf = self._get_crazyflie()

        await cf.param.validate()
        await cf.high_level_commander.enable()

        if needs_show_mode:
            await self._enable_show_mode()

    @staticmethod
    def _create_empty_preflight_status_report() -> PreflightCheckInfo:
        """Creates an empty preflight status report that will be updated
        periodically.
        """
        report = PreflightCheckInfo()
        report.add_item("battery", "Battery")
        report.add_item("stabilizer", "Sensors")
        report.add_item("kalman", "Kalman filter")
        report.add_item("positioning", "Positioning")
        report.add_item("home", "Home position")
        report.add_item("trajectory", "Trajectory and lights")
        return report

    async def _enable_show_mode(self) -> None:
        """Enables the drone-show mode on the Crazyflie."""
        cf = self._get_crazyflie()
        await cf.param.set("kalman.robustTdoa", 1)
        await cf.param.set("show.enabled", 1)
        if self.driver.use_test_mode:
            await cf.param.set("show.testing", 1)

    def _on_battery_and_system_state_received(self, message):
        self._battery.voltage = message.items[0]
        self._battery.charging = message.items[1] == 1  # PM state 1 = charging
        self._armed = bool(message.items[2])
        self._update_error_codes()
        self.update_status(battery=self._battery)
        self.notify_updated()

    def _on_position_velocity_info_received(self, message):
        self._position.x, self._position.y, self._position.z = message.items[0:3]
        self._velocity.x, self._velocity.y, self._velocity.z = message.items[3:6]

        self._position.x /= 1000
        self._position.y /= 1000
        self._position.z /= 1000

        self._velocity.x /= 1000
        self._velocity.y /= 1000
        self._velocity.z /= 1000

        self._update_error_codes()

        self.update_status(
            position_xyz=self._position,
            velocity_xyz=self._velocity,
            heading=message.items[6],
        )

        self.notify_updated()

    def _reset_status_variables(self) -> None:
        """Resets the status variables of the UAV, typically after connecting
        to the UAV or after re-establishing a connection.
        """
        self._preflight_status = self._create_empty_preflight_status_report()
        self._armed = True  # Crazyflies typically boot in an armed state
        self._fence_breached = False
        self._battery = BatteryInfo()
        self._position = PositionXYZ()
        self._show_execution_stage = DroneShowExecutionStage.UNKNOWN
        self._velocity = VelocityXYZ()

    def _setup_logging_session(self) -> LogSession:
        """Sets up the log blocks that contain the variables we need from the
        Crazyflie, and returns a LogSession object.
        """
        assert self._crazyflie is not None

        session = self._crazyflie.log.create_session()
        session.configure(graceful_cleanup=True)
        return session

        session.create_block(
            "pm.vbat",
            "pm.state",
            "sys.armed",
            period=1,
            handler=self._on_battery_and_system_state_received,
        )
        session.create_block(
            "stateEstimateZ.x",
            "stateEstimateZ.y",
            "stateEstimateZ.z",
            "stateEstimateZ.vx",
            "stateEstimateZ.vy",
            "stateEstimateZ.vz",
            "stateEstimate.yaw",
            period=1,
            handler=self._on_position_velocity_info_received,
        )
        return session

    def _update_error_codes(self) -> None:
        """Updates the set of error codes based on what we know about the current
        state of the drone.
        """
        self.ensure_error(
            FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS,
            present=(
                self._preflight_status.in_progress
                or self._show_execution_stage
                is DroneShowExecutionStage.WAIT_FOR_PREFLIGHT_CHECKS
            ),
        )
        self.ensure_error(
            FlockwaveErrorCode.PREARM_CHECK_FAILURE,
            present=self._preflight_status.failed_conclusively,
        )

        self.ensure_error(FlockwaveErrorCode.DISARMED, present=not self._armed)

        # TODO(ntamas): use GEOFENCE_VIOLATION_WARNING if the motors are not
        # running. Currently we have no information about whether the motors are
        # running or not.
        self.ensure_error(
            FlockwaveErrorCode.GEOFENCE_VIOLATION, present=self._fence_breached
        )

        self.ensure_error(
            FlockwaveErrorCode.TAKEOFF,
            present=self._show_execution_stage is DroneShowExecutionStage.TAKEOFF,
        )
        self.ensure_error(
            FlockwaveErrorCode.LANDING,
            present=self._show_execution_stage is DroneShowExecutionStage.LANDING,
        )
        self.ensure_error(
            FlockwaveErrorCode.LANDED,
            present=self._show_execution_stage is DroneShowExecutionStage.LANDED,
        )

        voltage = self._battery.voltage
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_LOW_ERROR,
            present=voltage is not None and voltage <= 3.1,
        )
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_LOW_WARNING,
            present=voltage is not None and (voltage <= 3.3 and voltage > 3.1),
        )

    def _update_preflight_status_from_result_codes(
        self, codes: Sequence[PreflightCheckStatus]
    ) -> None:
        """Updates the result of the local preflight check report data structure
        from the result codes received in a stauts package.
        """
        for check, code in zip(self._preflight_status.items, codes):
            code = code & 0x03
            if code == 3 and check.id == "kalman":
                # The Kalman filter is a soft failure only; the drone is
                # constantly attempting to bring the filter back into a
                # convergent state
                code = 4
            check.result = self._preflight_result_map[code]

        self._preflight_status.update_summary()

    async def _upload_light_program(self, data: bytes) -> None:
        """Uploads the given light program to the Crazyflie drone."""
        cf = self._get_crazyflie()
        try:
            memory = await cf.mem.find(MemoryType.APP)
        except ValueError:
            raise RuntimeError("Light programs are not supported on this drone")
        addr = await write_with_checksum(memory, 0, data, only_if_changed=True)
        await cf.run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.DEFINE_LIGHT_PROGRAM,
            data=Struct("<BBBBII").pack(
                0,  # light program ID
                LightProgramLocation.MEM,
                LightProgramType.SKYBRUSH,
                0,  # fps, not used
                addr,  # address in memory
                len(data),  # length of light program
            ),
        )

    async def _upload_trajectory_and_fence(
        self,
        trajectory: TrajectorySpecification,
        home: Optional[Tuple[float, float, float]],
        fence_config: FenceConfiguration,
    ) -> None:
        """Uploads the given trajectory data to the Crazyflie drone and applies
        the given safety fence configuration.
        """
        cf = self._get_crazyflie()

        try:
            trajectory_memory = await cf.mem.find(MemoryType.TRAJECTORY)
        except ValueError:
            raise RuntimeError("Trajectories are not supported on this drone")

        supports_fence = await self.fence.is_supported() if self.fence else False
        if not supports_fence:
            self.driver.log.warning(
                "Geofence is not supported on this drone; please update the firmware",
                extra={"id": self.id},
            )

        # Define the home position and the takeoff time first
        await self.set_home_position(home or trajectory.home_position)
        await self.set_parameter("show.takeoffTime", trajectory.takeoff_time)

        # Set the landing height
        await self.set_parameter("show.landingHeight", trajectory.landing_height)

        # Encode the trajectory and write it to the Crazyflie memory
        data = encode_trajectory(trajectory, encoding=TrajectoryEncoding.COMPRESSED)
        addr = await write_with_checksum(
            trajectory_memory, 0, data, only_if_changed=True
        )

        # Define the geofence first (for safety reasons)
        if supports_fence:
            assert self.fence is not None
            await fence_config.apply(self.fence, trajectory)

        # Now we can define the entire trajectory as well
        await cf.high_level_commander.define_trajectory(
            0, addr=addr, type=TrajectoryType.COMPRESSED
        )


class CrazyflieHandlerTask:
    """Class responsible for handling communication with a single Crazyflie
    drone.
    """

    _debug: bool
    _log: Logger
    _status_interval: float
    _uav: CrazyflieUAV
    _use_fake_position: Optional[Tuple[float, float, float]]

    def __init__(
        self,
        uav: CrazyflieUAV,
        log: Logger,
        debug: bool = False,
        status_interval: float = 0.5,
        use_fake_position: Optional[Tuple[float, float, float]] = None,
    ):
        """Constructor.

        Parameters:
            uav: the Crazyflie UAV to communicate with
            debug: whether to log the communication with the UAV on the console
            status_interval: number of seconds that should pass between consecutive
                status requests sent to a drone
            use_fake_position: whether to feed a fake position to the UAV as if
                it was received from an external positioning system. Strictly
                for debugging purposes.
        """
        self._log = log
        self._uav = uav
        self._debug = bool(debug)
        self._status_interval = float(status_interval)
        self._use_fake_position = use_fake_position

    async def run(self) -> None:
        """Executes the task that handles communication with the associated
        Crazyflie drone.

        This task is guaranteed not to throw an exception so it won't crash the
        parent nursery it is running in. However, it will not handle
        reconnections either -- it will simply exit in case of a connection
        error.
        """
        try:
            await self._run()
        except IOError as ex:
            self._log.error(
                f"Error while handling Crazyflie: {str(ex)}",
                extra={"id": self._uav.id, "telemetry": "ignore"},
            )
            # We do not log the stack trace of IOErrors -- the stack trace is too long
            # and in 99% of the cases it is simply a communication error
        except Exception as ex:
            self._log.exception(
                f"Error while handling Crazyflie: {str(ex)}", extra={"id": self._uav.id}
            )

    async def _run(self) -> None:
        """Implementation of the task itself.

        This task is guaranteed not to throw an exception so it won't crash the
        parent nursery it is running in. However, it will not handle
        reconnections either -- it will simply exit in case of a connection
        error.
        """
        self._uav._reset_status_variables()

        try:
            async with AsyncExitStack() as stack:
                enter = stack.enter_async_context

                try:
                    await enter(self._uav.use(debug=self._debug))
                    assert self._uav.log_session is not None
                    await enter(self._uav.log_session)
                except TimeoutError:
                    self._log.error(
                        "Communication timeout while initializing connection",
                        extra={"id": self._uav.id, "telemetry": "ignore"},
                    )
                    return
                except Exception as ex:
                    self._log.error(
                        f"Error while initializing connection: {str(ex)}",
                        extra={"id": self._uav.id},
                    )
                    if not isinstance(ex, IOError):
                        self._log.exception(ex)
                    else:
                        # We do not log IOErrors -- the stack trace is too long
                        # and in 99% of the cases it is simply a communication error
                        pass
                    return

                nursery: Nursery = await enter(open_nursery())  # type: ignore
                self._uav.notify_shutdown_suspend_or_reboot = (
                    nursery.cancel_scope.cancel
                )
                nursery.start_soon(self._uav.process_console_messages)
                nursery.start_soon(
                    self._uav.process_drone_show_status_messages, self._status_interval
                )
                nursery.start_soon(self._uav.process_log_messages)

                if self._use_fake_position:
                    nursery.start_soon(self._feed_fake_position)

                await self._reupload_last_show_if_needed()

                # We need to set up the flight mode here after a bit of delay,
                # otherwise we try to set it too fast after a reboot and the
                # drone will ignore the request
                try:
                    await sleep(2)
                    await self._uav.setup_flight_mode()
                except TimeoutError:
                    self._log.error(
                        "Communication timeout while setting flight mode",
                        extra={"id": self._uav.id, "telemetry": "ignore"},
                    )
                    return
                except Exception as ex:
                    self._log.error(
                        f"Error while setting flight mode: {str(ex)}",
                        extra={"id": self._uav.id},
                    )
                    if not isinstance(ex, IOError):
                        self._log.exception(ex)
                    else:
                        # We do not log IOErrors -- the stack trace is too long
                        # and in 99% of the cases it is simply a communication error
                        pass
                    return
        finally:
            self._uav.notify_shutdown_suspend_or_reboot = nop

    async def _feed_fake_position(self) -> None:
        """Background task that feeds a fake position to the UAV as if it was
        coming from an external positioning system.
        """
        assert self._uav._crazyflie is not None
        async for _ in periodic(0.2):
            if self._use_fake_position:
                x, y, z = self._use_fake_position
                await self._uav._crazyflie._localization.send_external_position(x, y, z)

    async def _reupload_last_show_if_needed(self) -> None:
        try:
            if self._uav.has_previously_uploaded_show:
                # UAV was rebooted but we have already uploaded a show to it
                # before, so we should upload it again if the show framework
                # is in the idle state. First we wait two seconds to be sure
                # that we receive at least one show status packet from the
                # drone
                await sleep(2)
                if not self._uav.is_running_show:
                    await self._uav.reupload_last_show()
        except TimeoutError:
            # This is normal, it comes from aiocflib when the Crazyflie is
            # turned off again
            self._log.warn(
                "Failed to re-upload previously uploaded show to possibly "
                "rebooted drone due to a communication timeout",
                extra={"id": self._uav.id},
            )
        except Exception as ex:
            self._log.warn(
                "Failed to re-upload previously uploaded show to possibly rebooted drone",
                extra={"id": self._uav.id},
            )
            self._log.exception(ex)
