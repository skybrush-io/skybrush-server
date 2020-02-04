"""Driver class for virtual drones."""

from enum import Enum
from math import atan2, cos, hypot, sin, pi
from random import random, choice
from trio import CancelScope, sleep
from trio_util import periodic
from typing import Callable, Optional

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    GPSCoordinate,
    FlatEarthToGPSCoordinateTransformation,
    Vector3D,
)
from flockwave.server.concurrency import delayed
from flockwave.server.model.errors import FlockwaveErrorCode
from flockwave.server.model.uav import UAVBase, UAVDriver

from .battery import VirtualBattery


__all__ = ("VirtualUAVDriver", )


class VirtualUAVState(Enum):
    """Enum class that represents the possible states of a virtual UAV."""

    LANDED = 0
    TAKEOFF = 1
    AIRBORNE = 2
    LANDING = 3


class VirtualUAVDriver(UAVDriver):
    """Virtual UAV driver that manages a group of virtual UAVs provided by this
    extension.
    """

    def create_uav(self, id, center, radius, angle, angular_velocity):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id (str): the identifier of the UAV to create
            center (GPSCoordinate): the point around which the UAV will be
                circling
            radius (float): the radius of the circle; zero means that the
                UAV will not circle around the center point but will float
                over it
            angle (float): the initial angle of the UAV along the circle
            angular_velocity (float): the angular velocity of the UAV when
                circling, in radians per second

        Returns:
            VirtualUAV: an appropriate virtual UAV object
        """
        uav = VirtualUAV(id, driver=self)
        uav.angle = angle
        uav.angular_velocity = angular_velocity
        uav.cruise_altitude = center.agl
        uav.home = center.copy()
        uav.home.amsl = None
        uav.home.agl = 0
        uav.radius = radius
        uav.target = center
        return uav

    async def handle_command_arm(self, uav):
        uav.armed = True

    async def handle_command_disarm(self, uav):
        uav.armed = False

    def handle_command_error(self, uav, value=0):
        value = int(value)
        uav.user_defined_error = value
        return (
            f"Error code set to {uav.user_defined_error}"
            if uav.has_user_defined_error
            else "Error code cleared"
        )

    async def handle_command_timeout(self, uav):
        await sleep(1000000)

    async def handle_command_yo(self, uav):
        await sleep(0.5 + random())
        return "yo" + choice("?!.")

    def _send_fly_to_target_signal_single(self, uav, target):
        if uav.state == VirtualUAVState.LANDED:
            uav.takeoff()
            if target.agl is None and target.amsl is None:
                target.agl = uav.cruise_altitude
        uav.target = target
        uav.clear_radius()

    def _send_landing_signal_single(self, uav):
        uav.land()
        return True

    def _send_reset_signal_single(self, uav, component):
        if not component:
            # Resetting the whole UAV, this is supported
            uav.reset()
        else:
            # No components on this UAV
            return False

    def _send_shutdown_signal_single(self, uav):
        uav.shutdown()
        return True

    def _send_takeoff_signal_single(self, uav):
        uav.takeoff()
        return True


class VirtualUAV(UAVBase):
    """Model object representing a virtual UAV provided by this extension.

    The virtual UAV will circle around a given target position by default, with
    a given angular velocity and a given radius. The radius is scaled down
    to half of the original radius during landing (this is to make it easier
    to see the landing process on the web UI even if the altitude display
    is not shown or not implemented). Similarly, the radius is scaled up
    from half the original size to the full radius during takeoff.

    The UAV may be given a new target position (and a new altitude), in
    which case it will approach the new target with a reasonable maximum
    velocity. No attempts are made to make the acceleration realistic.

    Attributes:
        angle (float): the current angle of the UAV on the circle around the
            target.
        angular_velocity (float): the angular velocity of the UAV along the
            circle around the target
        cruise_altitude (float): the altitude (relative to home) where the
            UAV will consider a take-off attempt as finished
        error (int): the simulated error code of the UAV; zero if there is
            no error.
        has_user_defined_error (bool): whether the UAV currently has at least
            one user-defined (simulated) non-zero error code
        home (GPSCoordinate): the home coordinate of the UAV and the origin
            of the flat Earth transformation that the UAV uses. Altitude
            component is used to define the cruise altitude where a take-off
            attempt will stop.
        max_ascent_rate (float): the maximum ascent rate of the UAV along
            the Z axis (perpendicular to the surface of the Earth), in
            metres per second
        max_velocity (float): the maximum velocity of the UAV along the
            X-Y plane (parallel to the surface to the Earth), in metres
            per second
        position (GPSCoordinate): the current position of the center of the
            circle that the UAV traverses; altitude is given as relative to
            home.
        radius (float): the radius of the circle. Set it to zero to get rid
            of the circling behaviour; in this case, the UAV will float
            statically above the target position.
        state (VirtualUAVState): the state of the UAV
        target (GPSCoordinate): the target coordinates of the UAV; altitude
            must be given as relative to home. Note that this is not the
            coordinate that the UAV will reach; this is the coordinate of
            the center of the circle that the UAV will traverse when it
            reaches its destination.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        self._armed = True  # will be disarmed when booting
        self._autopilot_initializing = False
        self._pos_flat = Vector3D()
        self._pos_flat_circle = FlatEarthCoordinate()
        self._state = None
        self._target_xyz = None
        self._trans = FlatEarthToGPSCoordinateTransformation(
            origin=GPSCoordinate(lat=0, lon=0)
        )
        self._transition_progress = 0.0
        self._user_defined_error = None

        self._request_shutdown = None
        self._shutdown_reason = None

        self.angle = 0.0
        self.angular_velocity = 0.0
        self.cruise_altitude = 20
        self.errors = []
        self.max_ascent_rate = 2
        self.max_velocity = 10
        self.radiation_ext = None
        self.radius = 0.0
        self.state = VirtualUAVState.LANDED
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

    def clear_radius(self):
        if self.radius > 0:
            self._pos_flat.x = self._pos_flat_circle.x
            self._pos_flat.y = self._pos_flat_circle.y
            self._pos_flat.z = self._pos_flat_circle.agl
            self.radius = 0

    @property
    def has_user_defined_error(self):
        return bool(self._user_defined_error)

    @property
    def home(self):
        return self._trans.origin

    @home.setter
    def home(self, value):
        self._trans.origin = value

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

        self._state = value
        self._transition_progress = 0.0

    @property
    def target(self):
        """The target coordinates of the UAV."""
        return self._target

    @target.setter
    def target(self, value):
        self._target = value
        if self._target is None:
            self._target_xyz = None
            return

        # Calculate the real altitude component of the target
        new_altitude = self._pos_flat.z if value.agl is None else value.agl

        # Update the target and its XYZ representation
        self._target.update(agl=new_altitude)
        flat = self._trans.to_flat_earth(value)
        self._target_xyz = Vector3D(x=flat.x, y=flat.y, z=new_altitude)

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

    def land(self):
        """Starts a simulated landing with the virtual UAV."""
        if self.state != VirtualUAVState.AIRBORNE:
            return

        if self._target_xyz is None:
            self._target_xyz = self._pos_flat.copy()
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

    def step(self, dt, mutator=None):
        """Simulates a single step of the trajectory of the virtual UAV based
        on its state and the amount of time that has passed.

        Parameters:
            dt (float): the time that has passed, in seconds.
            mutator (DeviceTreeMutator): the mutator object that should be
                used by the UAV to update its channel nodes
        """
        state = self._state

        # Update the angle
        if state == VirtualUAVState.AIRBORNE and self.radius > 0:
            # When airborne and the circle radius is positive, the UAV is
            # circling in the air with a prescribed angular velocity.
            # Otherwise, the angle does not change.
            self.angle += self.angular_velocity * dt

        # Do we have a target?
        if self._target_xyz is not None:
            # We aim for the target in the XYZ plane only if we are airborne
            if state == VirtualUAVState.AIRBORNE:
                dx = self._target_xyz.x - self._pos_flat.x
                dy = self._target_xyz.y - self._pos_flat.y
            else:
                dx, dy = 0, 0

            if state != VirtualUAVState.LANDED:
                dz = self._target_xyz.z - self._pos_flat.z
            else:
                dz = 0

            angle = atan2(dy, dx)
            dist = hypot(dx, dy)
            if dist < 1e-6:
                dist = 0

            displacement_xy = min(dist, dt * self.max_velocity)
            displacement_z = min(abs(dz), dt * self.max_ascent_rate)
            if dz < 0:
                displacement_z *= -1

            self._pos_flat.x += cos(angle) * displacement_xy
            self._pos_flat.y += sin(angle) * displacement_xy
            self._pos_flat.z += displacement_z

        # Scale the radius according to the progress of the transition if
        # we are currently in a transition
        if state in (VirtualUAVState.LANDING, VirtualUAVState.TAKEOFF):
            delta_progress = dt / 3
            remaining_progress = 1 - self._transition_progress
            if delta_progress < remaining_progress:
                self._transition_progress += delta_progress
            else:
                self._transition_progress = 1
            eased_progress = self._transition_progress
            if state == VirtualUAVState.LANDING:
                eased_progress = 1 - eased_progress
            radius = self.radius * (eased_progress + 1) / 2
        elif state == VirtualUAVState.LANDED:
            radius = self.radius * 0.5
        else:
            radius = self.radius

        # Finish the transition and enter the new state if needed
        if self._transition_progress >= 1:
            if state == VirtualUAVState.LANDING:
                self.state = VirtualUAVState.LANDED
            else:
                self.state = VirtualUAVState.AIRBORNE

        # Calculate our coordinates around the circle in flat Earth
        self._pos_flat_circle.x = self._pos_flat.x + cos(self.angle) * radius
        self._pos_flat_circle.y = self._pos_flat.y + sin(self.angle) * radius
        self._pos_flat_circle.agl = self._pos_flat.z
        self._pos_flat_circle.amsl = None

        # Transform the flat Earth coordinates to GPS around our
        # current position as origin
        position = self._trans.to_gps(self._pos_flat_circle)

        # Discharge the battery
        self.battery.discharge(dt, mutator)

        # Update the UAV status
        updates = {
            "position": position,
            "errors": self.errors,
            "battery": self.battery.status,
        }
        if self.radius > 0:
            updates["heading"] = self.angle / pi * 180 + 90
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
                {
                    "lat": position.lat,
                    "lon": position.lon,
                    "value": cos(self.angle) + 24.0,
                },
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

    def takeoff(self):
        """Starts a simulated take-off with the virtual UAV."""
        if self.state != VirtualUAVState.LANDED:
            return

        if self._target_xyz is None:
            self._target_xyz = self._pos_flat.copy()
        self._target_xyz.z = self.cruise_altitude
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

        self.armed = False
        self.autopilot_initializing = True

    def _notify_autopilot_initialized(self) -> None:
        """Notifies the virtual UAV that the autopilot has initialized."""
        self.autopilot_initializing = False

    def _notify_shutdown(self) -> None:
        """Notifies the virtual UAV that it is about to shut down."""
        self._request_shutdown = None
        self._shutdown_reason = None

        self.autopilot_initializing = False
