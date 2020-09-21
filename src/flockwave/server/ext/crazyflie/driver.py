"""Driver class for Crazyflie drones."""

from collections import defaultdict
from contextlib import asynccontextmanager, AsyncExitStack
from functools import partial
from pathlib import Path
from trio import open_memory_channel, open_nursery
from trio_util import periodic
from typing import List, Optional

from aiocflib.crazyflie import Crazyflie
from aiocflib.crazyflie.log import LogSession
from aiocflib.crazyflie.mem import write_with_checksum

from flockwave.gps.vectors import VelocityNED
from flockwave.server.ext.logger import log as base_log
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.uav import BatteryInfo, UAVBase, UAVDriver, VersionInfo
from flockwave.spec.ids import make_valid_object_id

from skybrush import get_skybrush_light_program_from_show_specification

from .crtp_extensions import (
    DRONE_SHOW_PORT,
    DroneShowCommand,
    DroneShowStatus,
    LightProgramLocation,
    LightProgramType,
    LIGHT_PROGRAM_MEMORY_ID,
    PREFLIGHT_STATUS_LIGHT_EFFECT,
)

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

    async def handle_command___show_upload(self, uav: "CrazyflieUAV", *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        await uav.handle_show_upload(show)

    def _request_preflight_report_single(self, uav) -> PreflightCheckInfo:
        return uav.preflight_status

    async def _request_version_info_single(self, uav) -> VersionInfo:
        return await uav.get_version_info()

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
    ]

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.uri = None
        self.notify_updated = None

        self._command_queue_tx, self._command_queue_rx = open_memory_channel(0)
        self._crazyflie = None
        self._log_session = None
        self._preflight_status = self._create_preflight_status_report()

        self._trajectory = None
        self._light_program = None

        self._battery = BatteryInfo()
        self._velocity = VelocityNED()

    async def get_version_info(self) -> VersionInfo:
        return {"firmware": await self._crazyflie.platform.get_firmware_version()}

    async def handle_show_upload(self, show) -> None:
        from trio import sleep

        light_program = get_skybrush_light_program_from_show_specification(show)
        await self._upload_light_program(light_program)

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
        async with self._command_queue_rx:
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
                self._update_preflight_status_from_result_codes(status.preflight_checks)

                # TODO(ntamas): store local position somewhere
                self.update_status(battery=self._battery)

    async def process_incoming_log_messages(self) -> None:
        """Runs a task that processes incoming log messages and calls the
        appropriate log message handlers.
        """
        await self._log_session.process_messages()

    async def reboot(self):
        """Reboots the UAV."""
        return await self._crazyflie.reboot()

    async def run(self):
        """Starts the main message handler task of the UAV."""
        await CrazyflieHandlerTask(self, debug=self.driver.debug).run()

    async def shutdown(self):
        """Shuts down the UAV."""
        return await self._crazyflie.shutdown()

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
                self._crazyflie.param.set_and_restore("show.enabled", 1, 0)
            )
            await stack.enter_async_context(
                self._crazyflie.param.set_and_restore(
                    "ring.effect", PREFLIGHT_STATUS_LIGHT_EFFECT
                )
            )
            yield

    @staticmethod
    def _create_preflight_status_report() -> PreflightCheckInfo:
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
        self.update_status(battery=self._battery)

        self.notify_updated()

    def _on_position_velocity_info_received(self, message):
        # TODO(ntamas): store local position somewhere
        # TODO(ntamas): we should use a separate velocity field in the status
        # because we are posting velocity in the local frame, not NED
        self._velocity.x, self._velocity.y, self._velocity.z = message.items[3:6]
        self.update_status(velocity=self._velocity)
        self.notify_updated()

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

    def _update_preflight_status_from_result_codes(self, codes: List[int]) -> None:
        """Updates the result of the local preflight check report data structure
        from the result codes received in a stauts package.
        """
        for check, code in zip(self._preflight_status.items, codes):
            check.result = self._preflight_result_map[code & 0x03]
        self._preflight_status.update_summary()

    async def _upload_light_program(self, data: bytes) -> None:
        """Uploads the given light program to the Crazyflie drone."""
        try:
            memory = await self._crazyflie.mem.find(LIGHT_PROGRAM_MEMORY_ID)
        except ValueError:
            raise RuntimeError("Light programs not supported on this drone")
        addr = await write_with_checksum(memory, 0, data, only_if_changed=True)
        await self._crazyflie.run_command(
            port=DRONE_SHOW_PORT, command=DroneShowCommand.DEFINE_LIGHT_PROGRAM,
            data=[
                0, # light program ID
                LightProgramLocation.MEM,
                LightProgramType.SKYBRUSH,
                0, # fps, not used
                addr, # address in memory
                len(data) # length of light program
            ]
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
