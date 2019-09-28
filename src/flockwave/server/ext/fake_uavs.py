"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from __future__ import absolute_import, division

from enum import Enum
from math import atan2, cos, hypot, sin, pi
from random import random, choice
from trio import sleep
from trio_util import periodic

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    GPSCoordinate,
    FlatEarthToGPSCoordinateTransformation,
    Vector3D,
)
from flockwave.server.model.uav import BatteryInfo, UAVBase, UAVDriver
from flockwave.spec.ids import make_valid_uav_id

from .base import UAVExtensionBase


__all__ = ()


class FakeUAVDriver(UAVDriver):
    """Fake UAV driver that manages a group of fake UAVs provided by this
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
            FakeUAV: an appropriate fake UAV object
        """
        uav = FakeUAV(id, driver=self)
        uav.angle = angle
        uav.angular_velocity = angular_velocity
        uav.cruise_altitude = center.agl
        uav.home = center.copy()
        uav.home.amsl = None
        uav.home.agl = 0
        uav.radius = radius
        uav.target = center
        return uav

    def handle_command_error(self, uav, value=0):
        value = int(value)
        uav.error = value
        return (
            f"Error code set to {uav.error}" if uav.has_error else "Error code cleared"
        )

    async def handle_command_timeout(self, uav):
        await sleep(1000000)

    async def handle_command_yo(self, uav):
        await sleep(0.5 + random())
        return "yo" + choice("?!.")

    def _send_fly_to_target_signal_single(self, uav, target):
        if uav.state == FakeUAVState.LANDED:
            uav.takeoff()
            if target.agl is None and target.amsl is None:
                target.agl = uav.cruise_altitude
        uav.target = target

    def _send_landing_signal_single(self, uav):
        uav.land()
        return True

    def _send_takeoff_signal_single(self, uav):
        uav.takeoff()
        return True


class FakeUAVState(Enum):
    """Enum class that represents the possible states of a fake UAV."""

    LANDED = 0
    TAKEOFF = 1
    AIRBORNE = 2
    LANDING = 3


class FakeBattery(object):
    """A fake battery with voltage limits, linear discharge and a magical
    automatic recharge when it is about to be depleted.
    """

    def __init__(self, min_voltage=9, max_voltage=12.3, discharge_time=120):
        """Constructor.

        Parameters:
            min_voltage (float): the minimum voltage of the battery when it
                will magically recharge
            max_voltage (float): the maximum voltage of the battery
            discharge_time (float): number of seconds after which the battery
                becomes discharged
        """
        self._status = BatteryInfo()
        self._voltage_channel = None

        self._min = float(min_voltage)
        self._max = float(max_voltage)
        if self._max < self._min:
            self._min, self._max = self._max, self._min

        self._range = self._max - self._min

        self._discharge_rate = self._range / discharge_time

        self.voltage = random() * self._range + self._min

    @property
    def status(self):
        """The general status of the battery as a BatteryInfo_ object."""
        return self._status

    @property
    def voltage(self):
        """The current voltage of the battery."""
        return self._status.voltage

    @voltage.setter
    def voltage(self, value):
        percentage = 100 * (value - self._min) / self._range
        self._status.voltage = value
        self._status.percentage = max(min(percentage, 100), 0)

    def recharge(self):
        """Recharges the battery to the maximum voltage."""
        self.voltage = self._max

    def discharge(self, dt, mutator):
        """Simulates the discharge of the battery over the given time
        period.

        Parameters:
            dt (float): the time that has passed
        """
        new_voltage = self.voltage - dt * self._discharge_rate
        while new_voltage < self._min:
            new_voltage += self._range
        self.voltage = new_voltage

        if mutator is not None:
            mutator.update(self._voltage_channel, self.voltage)

    def register_in_device_tree(self, node):
        """Registers the battery in the given device tree node of a UAV."""
        device = node.add_device("battery")
        self._voltage_channel = device.add_channel("voltage", type=float, unit="V")


class FakeUAV(UAVBase):
    """Model object representing a fake UAV provided by this extension.

    The fake UAV will circle around a given target position by default, with
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
        has_error (bool): whether the UAV currently has a non-zero error code
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
        state (FakeUAVState): the state of the UAV
        target (GPSCoordinate): the target coordinates of the UAV; altitude
            must be given as relative to home. Note that this is not the
            coordinate that the UAV will reach; this is the coordinate of
            the center of the circle that the UAV will traverse when it
            reaches its destination.
    """

    def __init__(self, *args, **kwds):
        super(FakeUAV, self).__init__(*args, **kwds)

        self._pos_flat = Vector3D()
        self._pos_flat_circle = FlatEarthCoordinate()
        self._state = None
        self._target_xyz = None
        self._trans = FlatEarthToGPSCoordinateTransformation(
            origin=GPSCoordinate(lat=0, lon=0)
        )
        self._transition_progress = 0.0

        self.angle = 0.0
        self.angular_velocity = 0.0
        self.cruise_altitude = 20
        self.error = 0
        self.max_ascent_rate = 2
        self.max_velocity = 10
        self.radiation_ext = None
        self.radius = 0.0
        self.state = FakeUAVState.LANDED
        self.target = None

        self.step(0)

    @property
    def has_error(self):
        return self.error > 0

    @property
    def home(self):
        return self._trans.origin

    @home.setter
    def home(self, value):
        self._trans.origin = value

    @property
    def state(self):
        """The state of the UAV; one of the constants from the FakeUAVState_
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

    def land(self):
        """Starts a simulated landing with the fake UAV."""
        if self.state != FakeUAVState.AIRBORNE:
            return

        if self._target_xyz is None:
            self._target_xyz = self._pos_flat.copy()
        self._target_xyz.z = 0
        self.state = FakeUAVState.LANDING

    def step(self, dt, mutator=None):
        """Simulates a single step of the trajectory of the fake UAV based
        on its state and the amount of time that has passed.

        Parameters:
            dt (float): the time that has passed, in seconds.
            mutator (DeviceTreeMutator): the mutator object that should be
                used by the UAV to update its channel nodes
        """
        state = self._state

        # Update the angle
        if state != FakeUAVState.AIRBORNE and self.radius > 0:
            # When airborne and the circle radius is positive, the UAV is
            # circling in the air with a prescribed angular velocity.
            # Otherwise, the angle does not change.
            self.angle += self.angular_velocity * dt

        # Do we have a target?
        if self._target_xyz is not None:
            # We aim for the target in the XYZ plane only if we are airborne
            if state == FakeUAVState.AIRBORNE:
                dx = self._target_xyz.x - self._pos_flat.x
                dy = self._target_xyz.y - self._pos_flat.y
            else:
                dx, dy = 0, 0

            if state != FakeUAVState.LANDED:
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
        if state in (FakeUAVState.LANDING, FakeUAVState.TAKEOFF):
            delta_progress = dt / 3
            remaining_progress = 1 - self._transition_progress
            if delta_progress < remaining_progress:
                self._transition_progress += delta_progress
            else:
                self._transition_progress = 1
            eased_progress = self._transition_progress
            if state == FakeUAVState.LANDING:
                eased_progress = 1 - eased_progress
            radius = self.radius * (eased_progress + 1) / 2
        elif state == FakeUAVState.LANDED:
            radius = self.radius * 0.5
        else:
            radius = self.radius

        # Finish the transition and enter the new state if needed
        if self._transition_progress >= 1:
            if state == FakeUAVState.LANDING:
                self.state = FakeUAVState.LANDED
            else:
                self.state = FakeUAVState.AIRBORNE

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
            "error": self.error if self.has_error else (),
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
        """Starts a simulated take-off with the fake UAV."""
        if self.state != FakeUAVState.LANDED:
            return

        if self._target_xyz is None:
            self._target_xyz = self._pos_flat.copy()
        self._target_xyz.z = self.cruise_altitude
        self.state = FakeUAVState.TAKEOFF

    def _initialize_device_tree_node(self, node):
        self.battery = FakeBattery()
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


class FakeUAVProviderExtension(UAVExtensionBase):
    """Extension that creates one or more fake UAVs in the server.

    Fake UAVs circle around a given point in a given radius, with constant
    angular velocity. They are able to respond to landing and takeoff
    requests, and also handle the following commands:

    * Sending ``yo`` to a UAV makes it respond with either ``yo!``, ``yo?``
      or ``yo.``, with a mean delay of 500 milliseconds.

    * Sending ``timeout`` to a UAV makes it register the command but never
      finish its execution. Useful for testing the timeout and cancellation
      mechanism of the command execution manager of the server.
    """

    def __init__(self):
        """Constructor."""
        super(FakeUAVProviderExtension, self).__init__()
        self._delay = 1

        self.radiation = None
        self.uavs = []
        self.uav_ids = []

    def _create_driver(self):
        return FakeUAVDriver()

    def configure(self, configuration):
        # Get the number of UAVs to create and the format of the IDs
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "FAKE-{0}")

        # Set the status updater thread frequency
        self.delay = configuration.get("delay", 1)

        # Get the center of the circle
        center = configuration.get("center")
        center = GPSCoordinate(
            lat=center["lat"], lon=center["lon"], agl=center["agl"], amsl=None
        )

        # Get the radius and angular velocity from the configuration
        radius = float(configuration.get("radius", 10))
        omega = 2 * pi / configuration.get("time_of_single_cycle", 10)

        # Generate IDs for the UAVs and then create them
        self.uav_ids = [
            make_valid_uav_id(id_format.format(index)) for index in range(count)
        ]
        self.uavs = [
            self._driver.create_uav(
                id,
                center=center,
                radius=radius,
                angle=2 * pi / count * index,
                angular_velocity=omega,
            )
            for index, id in enumerate(self.uav_ids)
        ]

        # Get hold of the 'radiation' extension and associate it to all our
        # UAVs
        radiation_ext = self.app.extension_manager.import_api("radiation")
        for uav in self.uavs:
            uav.radiation_ext = radiation_ext

    @property
    def delay(self):
        """Number of seconds that must pass between two consecutive
        simulated status updates to the UAVs.
        """
        return self._delay

    @delay.setter
    def delay(self, value):
        self._delay = max(float(value), 0)

    async def worker(self, app, configuration, logger):
        """Main background task of the extension that updates the state of
        the UAVs periodically.
        """
        with app.uav_registry.use(*self.uavs):
            async for _ in periodic(self._delay):
                with self.create_device_tree_mutation_context() as mutator:
                    for uav in self.uavs:
                        uav.step(mutator=mutator, dt=self._delay)

                app.request_to_send_UAV_INF_message_for(self.uav_ids)


construct = FakeUAVProviderExtension
