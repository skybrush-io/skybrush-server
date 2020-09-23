"""Driver class for Crazyflie drones."""

from collections import defaultdict
from contextlib import asynccontextmanager, AsyncExitStack
from functools import partial
from pathlib import Path
from trio import open_memory_channel, open_nursery, sleep
from trio_util import periodic
from typing import Callable, List, Optional, Tuple, Union

from aiocflib.crazyflie import Crazyflie
from aiocflib.crtp.crtpstack import MemoryType
from aiocflib.crazyflie.high_level_commander import TrajectoryType
from aiocflib.crazyflie.log import LogSession
from aiocflib.crazyflie.mem import write_with_checksum

from flockwave.gps.vectors import Vector3D, VelocityNED
from flockwave.server.ext.logger import log as base_log
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.uav import BatteryInfo, UAVBase, UAVDriver, VersionInfo
from flockwave.spec.errors import FlockwaveErrorCode
from flockwave.spec.ids import make_valid_object_id

from skybrush import (
    get_skybrush_light_program_from_show_specification,
    get_skybrush_trajectory_from_show_specification,
    TrajectorySpecification,
)

from .crtp_extensions import (
    DRONE_SHOW_PORT,
    DroneShowCommand,
    DroneShowExecutionStage,
    DroneShowStatus,
    LightProgramLocation,
    LightProgramType,
    LIGHT_PROGRAM_MEMORY_ID,
    PREFLIGHT_STATUS_LIGHT_EFFECT,
)
from .trajectory import encode_trajectory, TrajectoryEncoding, to_poly4d_sequence

__all__ = ("CrazyflieDriver",)

log = base_log.getChild("crazyflie")


class CrazyflieDriver(UAVDriver):
    """Driver class for Crazyflie drones.

    Attributes:
        app (SkybrushServer): the app in which the driver lives
        id_format (str): Python format string that receives a numeric
            drone ID in the flock and returns its preferred formatted
            identifier that is used when the drone is registered in the
            server, or any other object that has a ``format()`` method
            accepting a single integer as an argument and returning the
            preferred UAV identifier
    """

    def __init__(
        self, app=None, id_format: str = "{0:02}", cache: Optional[Path] = None
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
        self.id_format = id_format

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
        return uav

    @property
    def cache_folder(self) -> str:
        """Returns the full path to a folder where the driver can store
        the parameter TOC files of the Crazyflie drones that it sees.
        """
        return self._cache_folder

    def get_or_create_uav(self, address_space, index: int) -> "CrazyflieUAV":
        """Retrieves the UAV with the given index in the given address space
        or creates one if the driver has not seen a UAV with the given index in
        the given address space yet.

        Parameters:
            address_space: the address space
            index: the index of the address within the address space

        Returns:
            an appropriate UAV object
        """
        uav_id_map = self._uav_ids_by_address_space.get(address_space)
        formatted_id = uav_id_map.get(index) if uav_id_map else None
        if formatted_id is None:
            formatted_id = make_valid_object_id(
                self.id_format.format(index, address_space)
            )
            self._uav_ids_by_address_space[address_space][index] = formatted_id

        uav = self.app.object_registry.add_if_missing(
            formatted_id, factory=self._create_uav
        )
        if uav.uri is None:
            uav.uri = address_space[index]

        return uav

    async def handle_command_home(self, uav):
        """Command that retrieves the current home position of the UAV."""
        home = await uav.get_home_position()
        if home is None:
            return "No home position yet"
        else:
            x, y, z = home
            return f"Home: [{x:.2f}, {y:.2f}, {z:.2f}] m"

    async def handle_command_param(
        self, uav, name: Optional[str] = None, value: Optional[Union[str, float]] = None
    ):
        """Command that retrieves or sets the value of a parameter on the UAV."""
        if not name:
            raise RuntimeError("Missing parameter name")

        name = str(name)
        if "=" in name and value is None:
            name, value = name.split("=", 1)

        if value is not None:
            try:
                value = float(value)
            except ValueError:
                raise RuntimeError(f"Invalid parameter value: {value}")
            if value.is_integer():
                value = int(value)
            try:
                await uav.set_parameter(name, value)
            except KeyError:
                raise RuntimeError(f"No such parameter: {name}")

        try:
            value = await uav.get_parameter(name, fetch=True)
            return f"{name} = {value}"
        except KeyError:
            raise RuntimeError(f"No such parameter: {name}")

    async def handle_command_test(self, uav, component: Optional[str] = None) -> None:
        """Runs a self-test on a component of the UAV."""
        if component == "motor":
            # TODO(ntamas): allow this only when the drone is on the ground!
            await uav.set_parameter("health.startPropTest", 1)
            return "Motor test started"
        elif component == "led":
            async with uav.set_and_restore_parameter("ring.effect", 8):
                await sleep(2)
            await uav.set_parameter("ring.headlightEnable", 0)
            return "LED test executed"
        else:
            return "Usage: test <led|motor>"

    async def handle_command_stop(self, uav):
        """Stops the motors of the UAV immediately."""
        await uav.stop()
        return "Motor stop signal sent"

    handle_command_motoroff = handle_command_stop

    async def handle_command___show_upload(self, uav: "CrazyflieUAV", *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        await uav.upload_show(show, remember=True)

    def _request_preflight_report_single(self, uav) -> PreflightCheckInfo:
        return uav.preflight_status

    async def _request_version_info_single(self, uav) -> VersionInfo:
        return await uav.get_version_info()

    async def _send_light_or_sound_emission_signal_single(
        self, uav, signals, duration
    ) -> None:
        if "light" in signals:
            return await uav.emit_light_signal()

    async def _send_reset_signal_single(self, uav, component):
        if not component:
            # Resetting the whole UAV, this is supported
            # TODO(ntamas): log blocks have to be re-configured after reboot
            return await uav.reboot()
        else:
            # No component resets are implemented on this UAV yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_shutdown_signal_single(self, uav):
        return await uav.shutdown()

    async def _send_takeoff_signal_single(self, uav):
        return await uav.takeoff()


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

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.uri = None
        self.notify_updated = None

        self._command_queue_tx, self._command_queue_rx = open_memory_channel(0)

        self._crazyflie = None
        self._log_session = None
        self._last_uploaded_show = None

        self._reset_status_variables()

    async def emit_light_signal(self) -> None:
        """Asks the UAV to emit a visible light signal from its LED ring to
        attract attention.
        """
        await self.set_parameter("ring.lightSignalTrigger", 1)

    async def get_home_position(self) -> Optional[Tuple[float, float, float]]:
        """Returns the current home position of the UAV."""
        x = await self.get_parameter("preflight.homeX", fetch=True)
        y = await self.get_parameter("preflight.homeY", fetch=True)
        z = await self.get_parameter("preflight.homeZ", fetch=True)
        if not x and not y and z <= -10000:
            return None
        else:
            return x / 1000, y / 1000, z / 1000

    async def get_parameter(self, name: str, fetch: bool = False) -> float:
        """Returns the value of a parameter from the Crazyflie."""
        return await self._crazyflie.param.get(name, fetch=fetch)

    async def get_version_info(self) -> VersionInfo:
        return {"firmware": await self._crazyflie.platform.get_firmware_version()}

    async def land(self, altitude: float = 0.0, velocity: float = 0.5):
        """Initiates a landing to the given altitude (absolute or relative).

        Parameters:
            altitude: the altitude to reach at the end of the landing operation,
                in meters
            velocity: the desired takeoff velocity, in meters per second
        """
        # TODO(ntamas): here we (ab)use the extra features of our firmware
        # TODO(ntamas): launch this in a separate background task and return
        # early with the result
        # TODO(ntamas): figure out how much time the landing will take
        # approximately and shut down the motors at the end
        await self._crazyflie.high_level_commander.land(altitude, duration=-velocity)

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

    async def process_command_queue(self) -> None:
        """Runs a task that processes the commands targeted to this UAV as they
        are placed in the incoming command queue of the UAV.

        The processor will not interleave the execution of commands; only one
        command will be executed at the same time.
        """
        # Don't put this in an async with() block; we don't want to close the
        # RX queue when the producer of the TX queue (i.e. the CrazyflieHandlerTask)
        # disappears, we want to keep on listening
        async for command, args in self._command_queue_rx:
            print(repr(command), repr(args))

    async def process_drone_show_status_messages(self, period: float = 0.5) -> None:
        """Runs a task that requests a drone show related status report from
        the Crazyflie drone repeatedly.

        Parameters:
            period: the number of seconds elapsed between consecutive status
                requests, in seconds
        """
        async for _ in periodic(period):
            try:
                status = await self._crazyflie.run_command(
                    port=DRONE_SHOW_PORT, command=DroneShowCommand.STATUS
                )
                status = DroneShowStatus.from_bytes(status)
            except TimeoutError:
                status = None

            if status:
                self._battery.charging = status.charging
                self._battery.voltage = status.battery_voltage
                self._battery.percentage = status.battery_percentage
                self._position.update(
                    x=status.position[0], y=status.position[1], z=status.position[2]
                )
                self._update_preflight_status_from_result_codes(status.preflight_checks)
                self._update_error_codes()
                self.update_status(
                    battery=self._battery,
                    mode=status.mode,
                    light=status.light,
                    position_xyz=self._position,
                )
                self.notify_updated()

    async def process_incoming_log_messages(self) -> None:
        """Runs a task that processes incoming log messages and calls the
        appropriate log message handlers.
        """
        await self._log_session.process_messages()

    async def reboot(self):
        """Reboots the UAV."""
        return await self._crazyflie.reboot()

    async def reupload_last_show(self) -> None:
        """Uploads the last show that was uploaded to this drone again."""
        if self.last_uploaded_show:
            await self.upload_show(self.last_uploaded_show, remember=True)

    async def run(self, disposer: Optional[Callable[[], None]] = None):
        """Starts the main message handler task of the UAV."""
        try:
            await CrazyflieHandlerTask(self, debug=self.driver.debug).run()
        finally:
            if disposer:
                disposer()

    async def set_parameter(self, name: str, value: float) -> None:
        """Sets the value of a parameter on the Crazyflie."""
        await self._crazyflie.param.set(name, value)

    @asynccontextmanager
    async def set_and_restore_parameter(self, name: str, value: float) -> None:
        """Context manager that sets the value of a parameter on the UAV upon
        entering the context and resets it upon exiting.
        """
        async with self._crazyflie.param.set_and_restore(name, value):
            yield

    async def set_home_position(
        self, pos: Optional[Tuple[float, float, float]]
    ) -> None:
        """Sets or clears the home position of the UAV.

        Parameters:
            pos: the home position of the UAV, in the local coordinate system.
                Units are in meters. `None` means to clear the home position.
        """
        if pos is None:
            x, y, z = 0, 0, -10000
        else:
            x, y, z = pos
            x = int(round(x * 1000))
            y = int(round(y * 1000))
            z = int(round(z * 1000))
        await self.set_parameter("preflight.homeX", x)
        await self.set_parameter("preflight.homeY", y)
        await self.set_parameter("preflight.homeZ", z)

    async def stop(self) -> None:
        """Stops the motors of the UAV immediately."""
        await self._crazyflie.commander.stop()
        await self._crazyflie.high_level_commander.stop()

    async def shutdown(self):
        """Shuts down the UAV."""
        return await self._crazyflie.shutdown()

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
        if relative:
            raise NotImplementedError("Not supported by the firmware yet")

        # TODO(ntamas): here we (ab)use the extra features of our firmware
        await self._crazyflie.high_level_commander.takeoff(altitude, duration=-velocity)

    async def upload_show(self, show, *, remember: bool = True) -> None:
        light_program = get_skybrush_light_program_from_show_specification(show)
        await self._upload_light_program(light_program)

        trajectory = get_skybrush_trajectory_from_show_specification(show)
        await self._upload_trajectory(trajectory)

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

        if debug and "+log" not in uri:
            uri = uri.replace("://", "+log://")

        try:
            async with Crazyflie(
                uri, cache=self.driver.cache_folder
            ) as self._crazyflie:
                await self._crazyflie.log.validate()
                try:
                    self._log_session = self._setup_logging_session()
                    yield self._crazyflie
                finally:
                    self._log_session = None
        finally:
            self._crazyflie = None

    @asynccontextmanager
    async def use_show_mode(self):
        """Returns a context manager that turns on "drone show mode" on the
        Crazyflie when entering the context and turns it off when exiting the
        context.
        """
        await self._crazyflie.param.validate()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                self._crazyflie.param.set_and_restore("commander.enHighLevel", 1, 0)
            )
            await stack.enter_async_context(
                self._crazyflie.param.set_and_restore("show.enabled", 1, 0)
            )
            await stack.enter_async_context(
                self._crazyflie.param.set_and_restore("show.testing", 1, 0)
            )
            await stack.enter_async_context(
                self._crazyflie.param.set_and_restore(
                    "ring.effect", PREFLIGHT_STATUS_LIGHT_EFFECT
                )
            )
            yield

    @staticmethod
    def _create_empty_preflight_status_report() -> PreflightCheckInfo:
        """Creates an empty preflight status report that will be updated
        periodically.
        """
        report = PreflightCheckInfo()
        report.add_item("battery", "Battery")
        report.add_item("stabilizer", "Stabilizer")
        report.add_item("kalman", "Kalman filter")
        report.add_item("positioning", "Positioning")
        report.add_item("home", "Home position")
        report.add_item("trajectory", "Trajectory")
        report.add_item("lights", "Light program")
        return report

    def _on_battery_state_received(self, message):
        self._battery.voltage = message.items[0]
        self._battery.charging = message.items[1] == 1  # PM state 1 = charging
        self._update_error_codes()
        self.update_status(battery=self._battery)
        self.notify_updated()

    def _on_position_velocity_info_received(self, message):
        self._velocity.x, self._velocity.y, self._velocity.z = message.items[3:6]
        self._update_error_codes()
        self.update_status(position_xyz=self._position, velocity_xyz=self._velocity)
        self.notify_updated()

    def _reset_status_variables(self) -> None:
        """Resets the status variables of the UAV, typically after connecting
        to the UAV or after re-establishing a connection.
        """
        self._preflight_status = self._create_empty_preflight_status_report()
        self._battery = BatteryInfo()
        self._position = Vector3D()
        self._show_execution_stage = DroneShowExecutionStage.UNKNOWN
        self._velocity = VelocityNED()

    def _setup_logging_session(self):
        """Sets up the log blocks that contain the variables we need from the
        Crazyflie, and returns a LogSession object.
        """
        assert self._crazyflie is not None

        session = self._crazyflie.log.create_session()
        session.create_block(
            "pm.vbat", "pm.state", period=1, handler=self._on_battery_state_received,
        )
        session.create_block(
            "stateEstimate.x",
            "stateEstimate.y",
            "stateEstimate.z",
            "stateEstimate.vx",
            "stateEstimate.vy",
            "stateEstimate.vz",
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
        self.ensure_error(FlockwaveErrorCode.BATTERY_LOW_ERROR, present=voltage <= 3.1)
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_LOW_WARNING,
            present=voltage <= 3.3 and voltage > 3.1,
        )

    def _update_preflight_status_from_result_codes(self, codes: List[int]) -> None:
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
        try:
            memory = await self._crazyflie.mem.find(LIGHT_PROGRAM_MEMORY_ID)
        except ValueError:
            raise RuntimeError("Light programs are not supported on this drone")
        addr = await write_with_checksum(memory, 0, data, only_if_changed=True)
        await self._crazyflie.run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.DEFINE_LIGHT_PROGRAM,
            data=[
                0,  # light program ID
                LightProgramLocation.MEM,
                LightProgramType.SKYBRUSH,
                0,  # fps, not used
                addr,  # address in memory
                len(data),  # length of light program
            ],
        )

    async def _upload_trajectory(self, trajectory: TrajectorySpecification) -> None:
        """Uploads the given trajectory data to the Crazyflie drone."""
        try:
            memory = await self._crazyflie.mem.find(MemoryType.TRAJECTORY)
        except ValueError:
            raise RuntimeError("Trajectories are not supported on this drone")

        # Define the home position and the takeoff time first
        await self.set_home_position(trajectory.home_position)
        await self.set_parameter("show.takeoffTime", trajectory.takeoff_time)

        # Encode the trajectory and write it to the Crazyflie memory
        data = encode_trajectory(
            to_poly4d_sequence(trajectory), encoding=TrajectoryEncoding.COMPRESSED
        )
        addr = await write_with_checksum(memory, 0, data, only_if_changed=True)

        # Now we can define the entire trajectory as well
        await self._crazyflie.high_level_commander.define_trajectory(
            0, addr=addr, type=TrajectoryType.COMPRESSED
        )


class CrazyflieHandlerTask:
    """Class responsible for handling communication with a single Crazyflie
    drone.
    """

    def __init__(self, uav: CrazyflieUAV, debug: bool = False):
        """Constructor.

        Parameters:
            uav: the Crazyflie UAV to communicate with
            debug: whether to log the communication with the UAV on the console
        """
        self._command_queue_tx = uav._command_queue_tx
        self._uav = uav
        self._debug = bool(debug)

    async def run(self):
        """Executes the task that handles communication with the associated
        Crazyflie drone.

        This task is guaranteed not to throw an exception so it won't crash the
        parent nursery it is running in. However, it will not handle
        reconnections either -- it will simply exit in case of a connection
        error.
        """
        try:
            await self._run()
        except Exception as ex:
            log.error(f"Error while handling Crazyflie {self._uav.id}: {str(ex)}")
            if not isinstance(ex, IOError):
                log.exception(ex)
            else:
                # We do not log IOErrors -- the stack trace is too long
                # and in 99% of the cases it is simply a communication error
                pass

        # TODO(ntamas): when the task stops, we have to notify the scanner
        # that it can resume recognizing this drone again

    async def _run(self):
        """Implementation of the task itself.

        This task is guaranteed not to throw an exception so it won't crash the
        parent nursery it is running in. However, it will not handle
        reconnections either -- it will simply exit in case of a connection
        error.
        """
        self._uav._reset_status_variables()

        async with AsyncExitStack() as stack:
            enter = stack.enter_async_context

            await enter(self._uav.use(debug=self._debug))
            await enter(self._uav.log_session)
            await enter(self._uav.use_show_mode())
            await enter(self._uav._command_queue_tx)

            nursery = await enter(open_nursery())
            nursery.start_soon(self._uav.process_incoming_log_messages)
            nursery.start_soon(self._uav.process_drone_show_status_messages)
            nursery.start_soon(self._uav.process_command_queue)

            """
            try:
                if self._uav.last_uploaded_show is not None:
                    # UAV was rebooted but we have already uploaded a show to it
                    # before, so we should upload it again
                    await self._uav.reupload_last_show()
            except Exception as ex:
                log.warn(
                    f"Failed to re-upload previously uploaded show to possibly rebooted drone {self._uav.id}"
                )
                log.exception(ex)
            """
