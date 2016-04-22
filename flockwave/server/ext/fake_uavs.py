"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from __future__ import absolute_import, division

from .base import UAVExtensionBase
from enum import Enum
from eventlet import sleep, spawn, spawn_after
from flask import copy_current_request_context
from flockwave.gps.vectors import Altitude, AltitudeReference, Vector3D, \
    FlatEarthCoordinate, GPSCoordinate, FlatEarthToGPSCoordinateTransformation
from flockwave.server.model.uav import UAVBase, UAVDriver
from math import atan2, cos, hypot, sin, pi
from random import random, choice
from time import time


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
            radius (float): the radius of the circle
            angle (float): the initial angle of the UAV along the circle
            angular_velocity (float): the angular velocity of the UAV when
                circling, in radians per second

        Returns:
            FakeUAV: an appropriate fake UAV object
        """
        uav = FakeUAV(id, driver=self)
        uav.angle = angle
        uav.angular_velocity = angular_velocity
        uav.cruise_altitude = center.alt.value
        uav.home = center
        uav.radius = radius
        return uav

    def handle_command_timeout(self, uavs):
        cmd_manager = self.app.command_execution_manager
        return {uav: cmd_manager.start() for uav in uavs}

    def handle_command_yo(self, uavs):
        cmd_manager = self.app.command_execution_manager
        result = {}
        for uav in uavs:
            result[uav] = receipt = cmd_manager.start()
            delay = 0.5 + random()
            response = "yo" + choice("?!.")
            spawn_after(delay, cmd_manager.finish, receipt, response)
        return result

    def send_landing_signal(self, uavs):
        result = {}
        for uav in uavs:
            uav.land()
            result[uav] = True
        return result

    def send_takeoff_signal(self, uavs):
        result = {}
        for uav in uavs:
            uav.takeoff()
            result[uav] = True
        return result


class FakeUAVState(Enum):
    """Enum class that represents the possible states of a fake UAV."""

    LANDED = 0
    TAKEOFF = 1
    AIRBORNE = 2
    LANDING = 3


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
        radius (float): the radius of the circle
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
        self.max_ascent_rate = 2
        self.max_velocity = 10
        self.radius = 0.0
        self.state = FakeUAVState.TAKEOFF
        self.target = None

        self.step(0)

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
        if value.alt is None:
            new_altitude = Altitude.relative_to_home(self._pos_flat.z)
        elif value.alt.reference != AltitudeReference.relative_to_home:
            raise ValueError("altitude must be specified relative to home")
        else:
            new_altitude = value.alt.copy()

        # Update the target and its XYZ representation
        self._target.update(altitude=new_altitude)
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

    def step(self, dt):
        """Simulates a single step of the trajectory of the fake UAV based
        on its state and the amount of time that has passed.

        Parameters:
            dt (float): the time that has passed, in seconds.
        """
        state = self._state

        # Update the angle
        if state != FakeUAVState.LANDED:
            # When not landed, the UAV is circling in the air with a
            # prescribed angular velocity. Otherwise, the angle does
            # not change.
            self.angle += self.angular_velocity * dt

        # Do we have a target?
        if self._target_xyz is not None:
            # We aim for the target in the XYZ plane only if we are airborne
            if state == FakeUAVState.AIRBORNE:
                dx = self._target_xyz.x - self._pos_flat.x
                dy = self._target_xyz.y - self._pos_flat.y
            else:
                dx, dy = 0, 0

            dz = self._target_xyz.z - self._pos_flat.z

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
        self._pos_flat_circle.alt = \
            Altitude.relative_to_home(self._pos_flat.z)

        # Transform the flat Earth coordinates to GPS around our
        # current position as origin
        self.update_status(position=self._trans.to_gps(
            self._pos_flat_circle))

    def takeoff(self):
        """Starts a simulated take-off with the fake UAV."""
        if self.state != FakeUAVState.LANDED:
            return

        if self._target_xyz is None:
            self._target_xyz = self._pos_flat.copy()
        self._target_xyz.z = self.cruise_altitude
        self.state = FakeUAVState.TAKEOFF


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
        self.uavs = []
        self.uav_ids = []
        self._status_updater = StatusUpdater(self)

    def _create_driver(self):
        return FakeUAVDriver()

    def configure(self, configuration):
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "FAKE-{0}")
        self._status_updater.delay = configuration.get("delay", 1)

        center = configuration.get("center")
        center = GPSCoordinate(
            lat=center["lat"], lon=center["lon"],
            alt=Altitude.relative_to_home(center["alt"])
        )

        radius = float(configuration.get("radius", 10))
        omega = 2 * pi / configuration.get("time_of_single_cycle", 10)

        self.uav_ids = [id_format.format(index) for index in xrange(count)]
        self.uavs = [
            self._driver.create_uav(id, center=center, radius=radius,
                                    angle=2 * pi / count * index,
                                    angular_velocity=omega)
            for index, id in enumerate(self.uav_ids)
        ]
        for uav in self.uavs:
            self.app.uav_registry.add(uav)

    def spindown(self):
        self._status_updater.stop()

    def spinup(self):
        self._status_updater.start()


class StatusUpdater(object):
    """Status updater object that manages a green thread that will report
    the status of the fake UAVs periodically.
    """

    def __init__(self, ext, delay=1):
        """Constructor."""
        self._thread = None
        self._delay = None
        self._started_at = time()
        self.delay = delay
        self.ext = ext

    def _create_status_notification(self):
        """Creates a single status notification message that is to be
        broadcast via the message hub. The notification will contain the
        status information of all the UAVs managed by this extension.
        """
        return self.ext.app.create_UAV_INF_message_for(self.ext.uav_ids)

    @property
    def delay(self):
        """Number of seconds that must pass between two consecutive
        simulated status updates to the UAVs.
        """
        return self._delay

    @delay.setter
    def delay(self, value):
        self._delay = max(float(value), 0)

    @property
    def running(self):
        """Returns whether the status reporter thread is running."""
        return self._thread is not None

    def start(self):
        """Starts the status reporter thread if it is not running yet."""
        if self.running:
            return

        status_updater = copy_current_request_context(
            self.update_and_report_status)
        self.stopping = False
        self._thread = spawn(status_updater)
        self._thread.link(self._on_thread_stopped)

    def stop(self):
        """Stops the status reporter thread if it is running."""
        if not self.running:
            return

        self.stopping = True

    def update_and_report_status(self):
        """Updates and reports the status of all the UAVs."""
        hub = self.ext.app.message_hub
        while not self.stopping:
            self._update_uavs()

            message = self._create_status_notification()
            hub.send_message(message)

            sleep(self._delay)

        for uav in self.ext.uavs:
            uav.state = FakeUAVState.LANDED
            uav.state = FakeUAVState.TAKEOFF

    def _update_uavs(self):
        """Updates the status of all the UAVs."""
        for uav in self.ext.uavs:
            uav.step(dt=self._delay)

    def _on_thread_stopped(self, thread):
        """Handler called when the status reporter thread stops."""
        self.stopping = False
        self._thread = None


construct = FakeUAVProviderExtension
