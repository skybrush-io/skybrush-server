"""Driver class for virtual drones."""

from colour import Color
from enum import Enum
from math import atan2, cos, hypot, sin
from random import random, choice
from time import monotonic
from trio import CancelScope, sleep
from trio_util import periodic
from typing import Callable, Optional

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    GPSCoordinate,
    FlatEarthToGPSCoordinateTransformation,
    Vector3D,
    VelocityNED,
)
from flockwave.server.concurrency import delayed
from flockwave.server.model.gps import GPSFixType
from flockwave.server.model.preflight import PreflightCheckResult, PreflightCheckInfo
from flockwave.server.model.uav import VersionInfo, UAVBase, UAVDriver
from flockwave.server.utils import color_to_rgb565
from flockwave.spec.errors import FlockwaveErrorCode

from skybrush import (
    get_coordinate_system_from_show_specification,
    get_light_program_from_show_specification,
)

from .battery import VirtualBattery
from .lights import DefaultLightController
from .trajectory import TrajectoryPlayer


__all__ = ("VirtualUAVDriver",)


class VirtualUAVState(Enum):
    """Enum class that represents the possible states of a virtual UAV."""

    LANDED = 0
    TAKEOFF = 1
    AIRBORNE = 2
    LANDING = 3


#: Dummy preflight check information object returned from all virtual UAVs
_dummy_preflight_check_info = PreflightCheckInfo()
_dummy_preflight_check_info.add_item("accel", "Accelerometer")
_dummy_preflight_check_info.add_item("compass", "Compass")
_dummy_preflight_check_info.add_item("ekf", "EKF")
_dummy_preflight_check_info.add_item("gps", "GPS fix")
_dummy_preflight_check_info.add_item("gyro", "Gyroscope")
_dummy_preflight_check_info.add_item("pressure", "Pressure sensor")
for item in _dummy_preflight_check_info.items:
    item.result = PreflightCheckResult.PASS
_dummy_preflight_check_info.update_summary()


class VirtualUAVDriver(UAVDriver):
    """Virtual UAV driver that manages a group of virtual UAVs provided by this
    extension.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.uavs_armed_after_boot = False

    def create_uav(self, id, home: GPSCoordinate, heading: float = 0):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id (str): the identifier of the UAV to create
            home (GPSCoordinate): the home position of the UAV

        Returns:
            VirtualUAV: an appropriate virtual UAV object
        """
        uav = VirtualUAV(id, driver=self)
        uav.boots_armed = bool(self.uavs_armed_after_boot)
        uav.takeoff_altitude = 3
        uav.home = home.copy()
        uav.home.amsl = None
        uav.home.agl = 0
        uav.target = home.copy()
        uav.update_status(heading=heading)
        return uav

    async def handle_command_arm(self, uav):
        """Command that arms the virtual drone if it is on the ground."""
        if uav.arm_if_on_ground():
            return "Armed"
        else:
            return "Failed to arm"

    async def handle_command_async_exception(self, uav):
        """Throws an synchronous exception."""
        await sleep(0.2)
        raise ValueError("Async exception raised")

    async def handle_command_battery(self, uav, value):
        """Command that sets the battery voltage to a given value."""
        if hasattr(value, "endswith") and value.endswith("%"):
            uav.battery.percentage = float(value[:-1])
            return f"Battery percentage set to {uav.battery.percentage:.2f}%"
        else:
            uav.battery.voltage = float(value)
            return f"Voltage set to {uav.battery.voltage:.2f}V"

    async def handle_command_color(self, uav, value=None):
        """Command that overrides the color of the simulated light of the
        UAV.
        """
        if not value:
            uav.handle_color_override(None)
            return "Color override turned off."
        else:
            color = Color(value)
            uav.handle_color_override(color)
            return f"Color set to {color}"

    async def handle_command_disarm(self, uav):
        """Command that disarms the virtual drone if it is on the ground."""
        if uav.disarm_if_on_ground():
            return "Disarmed"
        else:
            return "Failed to disarm"

    def handle_command_error(self, uav, value=0):
        """Sets or clears the error code of the virtual drone."""
        value = int(value)
        uav.user_defined_error = value
        return (
            f"Error code set to {uav.user_defined_error}"
            if uav.has_user_defined_error
            else "Error code cleared"
        )

    def handle_command_exception(self, uav):
        """Throws a synchronous exception."""
        raise ValueError("Sync exception raised")

    async def handle_command_progress(self, uav):
        """Dummy command that can be used to test progress reports sent
        during the execution of a command.

        The execution of this command takes five seconds. A progress report
        is sent every 500 milliseconds.
        """
        for i in range(10):
            await sleep(0.5)
        return "Result."

    async def handle_command_timeout(self, uav):
        """Dummy command that does not respond in a reasonable amount of time.
        Can be used on the client side to test response timeouts.
        """
        await sleep(1000000)

    async def handle_command___show_upload(self, uav, *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        uav.handle_show_upload(show)
        await sleep(0.25 + random() * 0.5)

    async def handle_command_yo(self, uav):
        await sleep(0.5 + random())
        return "yo" + choice("?!.")

    def _request_preflight_report_single(self, uav) -> PreflightCheckInfo:
        return _dummy_preflight_check_info

    def _request_version_info_single(self, uav) -> VersionInfo:
        return {"firmware": self.app.version}

    def _send_fly_to_target_signal_single(self, uav, target) -> None:
        if uav.state == VirtualUAVState.LANDED:
            uav.takeoff()
            if target.agl is None and target.amsl is None:
                target.agl = uav.takeoff_altitude

        uav.stop_trajectory()
        uav.target = target

    async def _send_landing_signal_single(self, uav) -> None:
        # Make the landing signal async to simulate how it works for "real" drones
        await sleep(0.2)
        uav.land()

    def _send_light_or_sound_emission_signal_single(
        self, uav, signals, duration
    ) -> None:
        if "light" in signals:
            uav.handle_where_are_you(duration)

    def _send_motor_start_stop_signal_single(
        self, uav, start: bool, force: bool = False
    ) -> None:
        if start:
            uav.start_motors()
        else:
            uav.stop_motors()

    def _send_reset_signal_single(self, uav, component) -> None:
        if not component:
            # Resetting the whole UAV, this is supported
            uav.reset()
        else:
            # No components on this UAV
            raise RuntimeError(f"Resetting {component!r} is not supported")

    def _send_return_to_home_signal_single(self, uav) -> bool:
        if uav.state == VirtualUAVState.AIRBORNE:
            target = uav.home.copy()
            target.agl = uav.status.position.agl

            uav.stop_trajectory()
            uav.target = target

            uav.ensure_error(FlockwaveErrorCode.RETURN_TO_HOME)
        else:
            raise RuntimeError("UAV is not airborne, cannot start RTH")

    def _send_shutdown_signal_single(self, uav) -> bool:
        uav.shutdown()

    async def _send_takeoff_signal_single(
        self, uav, *, scheduled: bool = False
    ) -> None:
        await sleep(0.2)
        uav.takeoff()


class VirtualUAV(UAVBase):
    """Model object representing a virtual UAV provided by this extension.

    The virtual UAV will follow a given target position by default. The UAV may
    be given a new target position (and a new altitude), in which case it will
    approach the new target with a reasonable maximum velocity. No attempts are
    made to make the acceleration realistic.

    Attributes:
        error (int): the simulated error code of the UAV; zero if there is
            no error.
        has_user_defined_error (bool): whether the UAV currently has at least
            one user-defined (simulated) non-zero error code
        home (GPSCoordinate): the home coordinate of the UAV and the origin
            of the flat Earth transformation that the UAV uses. Altitude
            component is used to define the cruise altitude where a take-off
            attempt will stop.
        max_acceleration_xy (float): the maximum acceleration of the UAV in
            the X-Y plane (parallel to the surface of the Earth), in m/s2.
            To simplify the simulation a bit, the virtual UAVs are capable
            of infinite deceleration.
        max_acceleration_z (float): the maximum acceleration of the UAV in
            the X-Y plane (parallel to the surface of the Earth), in m/s2.
            To simplify the simulation a bit, the virtual UAVs are capable
            of infinite deceleration.
        max_velocity_xy (float): the maximum velocity of the UAV along the
            X-Y plane (parallel to the surface of the Earth), in m/s.
        max_velocity_z (float): the maximum ascent rate of the UAV along
            the Z axis (perpendicular to the surface of the Earth), in m/s.
        position (GPSCoordinate): the current position of the center of the
            circle that the UAV traverses; altitude is given as relative to
            home.
        state (VirtualUAVState): the state of the UAV
        target (GPSCoordinate): the target coordinates of the UAV; altitude
            must be given as relative to home. Note that this is not the
            coordinate that the UAV will reach; this is the coordinate of
            the center of the circle that the UAV will traverse when it
            reaches its destination.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        self._armed = True  # will be disarmed when booting if needed
        self._autopilot_initializing = False
        self._home_amsl = None
        self._light_controller = DefaultLightController(self)
        self._mission_started_at = None
        self._motors_running = False
        self._position_xyz = Vector3D()
        self._position_flat = FlatEarthCoordinate()
        self._state = None
        self._target_xyz = None
        self._trajectory = None
        self._trajectory_player = None
        self._trajectory_transformation = None
        self._trans = FlatEarthToGPSCoordinateTransformation(
            origin=GPSCoordinate(lat=0, lon=0)
        )
        self._user_defined_error = None
        self._velocity_xyz = Vector3D()
        self._velocity_ned = VelocityNED()

        self._request_shutdown = None
        self._shutdown_reason = None

        self.boots_armed = False
        self.errors = []
        self.max_acceleration_xy = 4
        self.max_acceleration_z = 1
        self.max_velocity_z = 2
        self.max_velocity_xy = 10
        self.radiation_ext = None
        self.state = VirtualUAVState.LANDED
        self.takeoff_altitude = 3
        self.target = None

        self.step(0)

    @property
    def armed(self) -> bool:
        """Returns whether the drone is armed."""
        return self._armed

    @armed.setter
    def armed(self, value: bool) -> None:
        self._armed = value
        self.ensure_error(FlockwaveErrorCode.DISARMED, present=not self._armed)

    def arm_if_on_ground(self) -> bool:
        """Arms the virtual drone if it is standing on the ground.

        Returns:
            whether the operation has succeeded
        """
        if self.state is VirtualUAVState.LANDED:
            self.armed = True
            return True
        else:
            return False

    def disarm_if_on_ground(self) -> bool:
        """Disarms the virtual drone if it is standing on the ground.

        Returns:
            whether the operation has succeeded
        """
        if self.state is VirtualUAVState.LANDED:
            self.armed = False
            return True
        else:
            return False

    @property
    def autopilot_initializing(self) -> bool:
        """Returns whether the simulated autopilot is currently initializing."""
        return self._autopilot_initializing

    @autopilot_initializing.setter
    def autopilot_initializing(self, value: bool) -> None:
        value = bool(value)
        if self._autopilot_initializing == value:
            return

        self._autopilot_initializing = value

        self.ensure_error(
            FlockwaveErrorCode.AUTOPILOT_INITIALIZING,
            present=self._autopilot_initializing,
        )

    @property
    def elapsed_time_in_mission(self):
        """Returns the number of seconds elapsed in the execution of the current
        mission (trajectory) or `None` if no mission is running yet.
        """
        return (
            monotonic() - self._mission_started_at
            if self._mission_started_at is not None
            else None
        )

    @property
    def has_trajectory(self):
        return self._trajectory is not None

    @property
    def has_user_defined_error(self):
        return bool(self._user_defined_error)

    @property
    def home(self):
        coord = self._trans.origin.copy()
        if self._home_amsl:
            coord.amsl = self._home_amsl
        return coord

    @home.setter
    def home(self, value):
        self._trans.origin = value
        if value.amsl is not None:
            self._home_amsl = float(value.amsl)

    @property
    def motors_running(self) -> bool:
        """Returns whether motors of the drone are running."""
        return self._motors_running

    @property
    def state(self):
        """The state of the UAV; one of the constants from the VirtualUAVState_
        enum class.
        """
        return self._state

    @state.setter
    def state(self, value):
        if self._state == value:
            return

        old_state = self._state
        self._state = value

        self.ensure_error(FlockwaveErrorCode.RETURN_TO_HOME, present=False)
        self.ensure_error(
            FlockwaveErrorCode.TAKEOFF, present=self._state is VirtualUAVState.TAKEOFF
        )
        self.ensure_error(
            FlockwaveErrorCode.LANDING, present=self._state is VirtualUAVState.LANDING
        )

        # Motors must be running if the UAV is not on the ground
        if self._state is not VirtualUAVState.LANDED:
            self.start_motors()

        if self._state is VirtualUAVState.TAKEOFF:
            if old_state is VirtualUAVState.LANDED:
                # Start following the trajectory if we have one
                if self._trajectory is not None:
                    self._trajectory_player = TrajectoryPlayer(self._trajectory)

                # Start the light program
                self._light_controller.play_light_program()
        elif self._state is VirtualUAVState.AIRBORNE:
            if old_state is not VirtualUAVState.TAKEOFF:
                # Stop following the trajectory, just in case
                self.stop_trajectory()
        elif self._state is VirtualUAVState.LANDED:
            # Mission ended, stop playing the light program and stop the motors
            self._light_controller.stop_light_program()
            self.stop_motors()

    @property
    def target(self):
        """The target coordinates of the UAV in GPS coordinates."""
        return self._target

    @target.setter
    def target(self, value):
        self._target = value

        # Clear the "return to home" error code (if any)
        self.ensure_error(FlockwaveErrorCode.RETURN_TO_HOME, present=False)

        if self._target is None:
            self._target_xyz = None
        else:
            # Calculate the real altitude component of the target
            new_altitude = self._position_xyz.z if value.agl is None else value.agl

            # Update the target and its XYZ representation
            self._target.update(agl=new_altitude)
            flat = self._trans.to_flat_earth(value)
            self._target_xyz = Vector3D(x=flat.x, y=flat.y, z=new_altitude)

    @property
    def target_xyz(self):
        """The target coordinates of the UAV in flat Earth coordinates around
        its home.
        """
        return self._target_xyz

    @target_xyz.setter
    def target_xyz(self, value):
        if value is None:
            self.target = None
        else:
            x, y, z = value
            amsl = self._home_amsl + z if self._home_amsl is not None else None
            flat_earth = FlatEarthCoordinate(x=x, y=y, amsl=amsl, agl=z)
            self.target = self._trans.to_gps(flat_earth)

    @property
    def user_defined_error(self) -> Optional[int]:
        """Returns the single user-defined error code or `None` if the UAV
        is currently not simulating any error condition.
        """
        return self._user_defined_error

    @user_defined_error.setter
    def user_defined_error(self, value: Optional[int]) -> None:
        if value is not None:
            value = int(value)

        if self._user_defined_error == value:
            return

        if self._user_defined_error is not None:
            self.ensure_error(self._user_defined_error, present=False)

        self._user_defined_error = value

        if self._user_defined_error is not None:
            self.ensure_error(self._user_defined_error, present=True)

    def ensure_error(self, code: int, present: bool = True) -> None:
        """Ensures that the given error code is present (or not present) in the
        error code list.

        Parameters:
            code: the code to add or remove
            present: whether to add the code (True) or remove it (False)
        """
        code = int(code)

        if code in self.errors:
            if not present:
                self.errors.remove(code)
        else:
            if present:
                self.errors.append(code)

    def handle_color_override(self, color: Optional[Color]) -> None:
        """Handles a command request to override the current color of the
        simulated light on the drone.
        """
        self._light_controller.override = color

    def handle_show_upload(self, show):
        """Handles the upload of a full drone show (trajectory + light program).

        Parameters:
            show: the uploaded show in Skybrush format

        Raises:
            RuntimeError: if the drone is not on the ground
        """
        if self.state is not VirtualUAVState.LANDED:
            raise RuntimeError("Cannot upload a show while the drone is airborne")

        self._trajectory_transformation = get_coordinate_system_from_show_specification(
            show
        )
        self._trajectory = show.get("trajectory", None)

        self._light_controller.load_light_program(
            get_light_program_from_show_specification(show)
        )

        self.update_status(mode="mission" if self.has_trajectory else "stab")

    def handle_where_are_you(self, duration):
        """Handles a 'where are you' command.

        Parameters:
            duration(int): duration of the signal in seconds.
        """
        self._light_controller.where_are_you(duration)

    def land(self):
        """Starts a simulated landing with the virtual UAV."""
        if self.state != VirtualUAVState.AIRBORNE:
            return

        if self._target_xyz is None:
            self._target_xyz = self._position_xyz.copy()
        self._target_xyz.z = 0
        self.state = VirtualUAVState.LANDING

    def reset(self):
        """Requests the UAV to reset itself if it is currently running."""
        if self._request_shutdown:
            self._shutdown_reason = "reset"
            self._request_shutdown()

    async def run_single_boot(
        self,
        delay: float,
        *,
        mutate: Callable,
        notify: Callable[[], None],
        spawn: Callable,
    ) -> str:
        """Simulates a single boot session of the virtual UAV.

        Parameters:
            delay: number of seconds to wait between consecutive status updates
            notify: function to call when new status information should be
                dispatched about the UAV
            spawn: function to call when the UAV wishes to spawn a background
                task

        Returns:
            `"shutdown"` if the user requested the UAV to shut down;
            `"reset"` if the user requested the UAV to reset itself.
        """
        # Booting takes a bit of time; we simulate this with a random delay
        await sleep(random() + 1)

        # Now we enter the main control loop of the UAV. We assume that the
        # autopilot initialization takes about 2 seconds.
        self._notify_booted()
        spawn(
            delayed(
                random() * 0.5 + 2,
                self._notify_autopilot_initialized,
                ensure_async=True,
            )
        )

        try:
            with CancelScope() as scope:
                self._request_shutdown = scope.cancel
                async for _ in periodic(delay):
                    with mutate() as mutator:
                        self.step(mutator=mutator, dt=delay)

                    notify()

            return self._shutdown_reason
        finally:
            self._notify_shutdown()

    def shutdown(self):
        """Requests the UAV to shutdown if it is currently running."""
        if self._request_shutdown:
            self._shutdown_reason = "shutdown"
            self._request_shutdown()

    def start_motors(self) -> None:
        """Starts the motors of the UAV if they are not running yet."""
        self._motors_running = True

    def stop_motors(self) -> None:
        """Stop the motors of the UAV if they are running and the UAV has
        landed.
        """
        if self.state is VirtualUAVState.LANDED:
            self._motors_running = False

    def step(self, dt: float, mutator=None) -> None:
        """Simulates a single step of the trajectory of the virtual UAV based
        on its state and the amount of time that has passed.

        Parameters:
            dt (float): the time that has passed, in seconds.
            mutator (DeviceTreeMutator): the mutator object that should be
                used by the UAV to update its channel nodes
        """
        state = self._state

        # Update the target of the drone if it is currently following a
        # predefined trajectory and it is not landing or landed
        if state is VirtualUAVState.TAKEOFF or state is VirtualUAVState.AIRBORNE:
            if self._trajectory_player and self._mission_started_at is not None:
                self._update_target_from_trajectory()

        # Do we have a target?
        if self._target_xyz is not None:
            # We aim for the target in the XY plane only if we are airborne
            if state is VirtualUAVState.AIRBORNE:
                dx = self._target_xyz.x - self._position_xyz.x
                dy = self._target_xyz.y - self._position_xyz.y
            else:
                dx, dy = 0, 0

            # During the takeoff phase, if we are flying a mission and the
            # takeoff time has not been reached yet, we are not allowed to
            # move in the Z direction either
            dz = self._target_xyz.z - self._position_xyz.z
            if state is VirtualUAVState.TAKEOFF and self._trajectory_player:
                t = self.elapsed_time_in_mission
                if t is not None and self._trajectory_player.is_before_takeoff(t):
                    dz = 0

            angle = atan2(dy, dx)
            dist_xy = hypot(dx, dy)
            if dist_xy < 1e-6:
                dist_xy = 0

            dist_z = abs(dz)

            reachable_velocity_xy = min(
                hypot(self._velocity_xyz.x, self._velocity_xyz.y)
                + self.max_acceleration_xy * dt,
                self.max_velocity_xy,
            )
            displacement_xy = min(dist_xy, dt * reachable_velocity_xy)

            displacement_x = cos(angle) * displacement_xy
            displacement_y = sin(angle) * displacement_xy

            if dz < 0:
                # Descending
                reachable_velocity_z = max(
                    self._velocity_xyz.z - self.max_acceleration_z * dt,
                    -self.max_velocity_z,
                )
                displacement_z = max(
                    dz, dt * reachable_velocity_z, -self._position_xyz.z
                )
            elif dz == 0:
                displacement_z = 0
            else:
                # Ascending
                reachable_velocity_z = min(
                    self._velocity_xyz.z + self.max_acceleration_z * dt,
                    self.max_velocity_z,
                )
                displacement_z = min(dz, dt * reachable_velocity_z)

            self._velocity_xyz.x = displacement_x / dt if dt > 0 else 0.0
            self._velocity_xyz.y = displacement_y / dt if dt > 0 else 0.0
            self._velocity_xyz.z = displacement_z / dt if dt > 0 else 0.0

            self._position_xyz.x += displacement_x
            self._position_xyz.y += displacement_y
            self._position_xyz.z += displacement_z

            # If we are above the takeoff altitude minus some threshold and
            # we are in the TAKEOFF stage, move to being airborne. Also, if
            # we are landing and we are very close to the ground, consider
            # ourselves as landed.
            eps = 0.2
            if state is VirtualUAVState.TAKEOFF:
                if self._position_xyz.z > max(eps, self.takeoff_altitude - eps):
                    self.state = VirtualUAVState.AIRBORNE
            elif state is VirtualUAVState.LANDING:
                if dist_z < eps * 0.5:
                    self.state = VirtualUAVState.LANDED
                    self._mission_started_at = None
                    self.target = None
            elif state is VirtualUAVState.AIRBORNE:
                # If we have reached the target, we can clear it
                if dist_xy < eps and dist_z < eps:
                    self.target = None

        # Calculate our coordinates in flat Earth
        self._position_flat.x = self._position_xyz.x
        self._position_flat.y = self._position_xyz.y
        self._position_flat.agl = self._position_xyz.z
        self._position_flat.amsl = (
            self._position_xyz.z + self._home_amsl
            if self._home_amsl is not None
            else None
        )

        # Transform the flat Earth coordinates to GPS around our
        # current position as origin
        position = self._trans.to_gps(self._position_flat)

        # Calculate the velocity in NED
        # TODO(ntamas): update the North/East components as well
        self._velocity_ned.update(down=-self._velocity_xyz.z)

        # Discharge the battery
        load = 0.01 if self.state is VirtualUAVState.LANDED else 1.0
        self.battery.discharge(dt, load, mutator=mutator)

        # Update the error code based on the battery status
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_CRITICAL, present=self.battery.is_critical
        )
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_LOW_ERROR, present=self.battery.is_very_low
        )
        self.ensure_error(
            FlockwaveErrorCode.BATTERY_LOW_WARNING, present=self.battery.is_low
        )

        # Update the error code based on whether the motors are running and the
        # UAV is airborne
        self.ensure_error(
            FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND,
            self.state is VirtualUAVState.LANDED and self.motors_running,
        )

        # Update the UAV status
        updates = {
            "position": position,
            "velocity": self._velocity_ned,
            "errors": self.errors,
            "battery": self.battery.status,
            "light": color_to_rgb565(self._light_controller.evaluate(monotonic())),
        }
        self.update_status(**updates)

        # Measure radiation if possible
        # TODO(ntamas): calculate radiation halfway between the current
        # position and the previous one instead
        if self.radiation_ext is not None and self.radiation_ext.loaded:
            observed_count = self.radiation_ext.measure_at(position, seconds=dt)
            # Okay, now we extrapolate from the observed count to the
            # per-second intensity. This should be made smarter; for
            # instance, we should report a new value only if we have
            # observed enough data
            radiation_intensity_estimate = observed_count / dt
        else:
            observed_count = 0
            radiation_intensity_estimate = 0

        # Also update our sensors
        if mutator is not None:
            mutator.update(
                self.thermometer,
                {"lat": position.lat, "lon": position.lon, "value": 24.0},
            )
            mutator.update(
                self.geiger_counter["averaged"],
                {
                    "lat": position.lat,
                    "lon": position.lon,
                    "value": radiation_intensity_estimate,
                },
            )
            mutator.update(
                self.geiger_counter["raw"],
                {"lat": position.lat, "lon": position.lon, "value": observed_count},
            )

    def stop_trajectory(self):
        """Prevents the UAV from following its pre-defined trajectory if it is
        currently following one. No-op if the UAV is not following a predefined
        trajectory.

        Also makes the UAV "forget" its current trajectory.
        """
        if self._trajectory_player:
            self._trajectory = None
            self._trajectory_player = None
            self._trajectory_transformation = None

    def takeoff(self):
        """Starts a simulated take-off with the virtual UAV."""
        if self.state != VirtualUAVState.LANDED:
            return

        if not self.armed:
            return

        self._mission_started_at = monotonic()

        if self._target_xyz is None:
            self._target_xyz = self._position_xyz.copy()
        self._target_xyz.z = self.takeoff_altitude

        self.state = VirtualUAVState.TAKEOFF

    def _initialize_device_tree_node(self, node):
        self.battery = VirtualBattery()
        self.battery.register_in_device_tree(node)

        device = node.add_device("thermometer")
        self.thermometer = device.add_channel(
            "temperature", type=object, unit="\u00b0C"
        )

        device = node.add_device("geiger_counter")
        self.geiger_counter = {
            "raw": device.add_channel("raw_measurement", type=object, unit="counts"),
            "averaged": device.add_channel(
                "measurement", type=object, unit="counts/sec"
            ),
        }

    def _notify_booted(self) -> None:
        """Notifies the virtual UAV that the boot process has ended."""
        self._request_shutdown = None
        self._shutdown_reason = None

        self.armed = bool(self.boots_armed)
        self.autopilot_initializing = True

        self._trajectory = None
        self._trajectory_player = None
        self._trajectory_transformation = None

        self.update_status(
            gps=GPSFixType.DGPS, mode="mission" if self.has_trajectory else "stab"
        )

    def _notify_autopilot_initialized(self) -> None:
        """Notifies the virtual UAV that the autopilot has initialized."""
        self.autopilot_initializing = False

    def _notify_shutdown(self) -> None:
        """Notifies the virtual UAV that it is about to shut down."""
        self._request_shutdown = None
        self._shutdown_reason = None

        self.autopilot_initializing = False

    def _update_target_from_trajectory(self) -> None:
        """Updates the target of the UAV based on the time elapsed since takeoff
        and the trajectory that it needs to follow.
        """
        t = self.elapsed_time_in_mission
        if not self._trajectory_player.is_before_takeoff(t):
            x, y, z = self._trajectory_player.position_at(t)
            self.target = self._trajectory_transformation.to_gps(
                FlatEarthCoordinate(x=x, y=y, agl=z)
            )
