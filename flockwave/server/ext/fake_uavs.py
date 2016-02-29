"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from __future__ import absolute_import, division

from .base import ExtensionBase
from enum import Enum
from eventlet.greenthread import sleep, spawn
from flask import copy_current_request_context
from flockwave.gps.vectors import Altitude, FlatEarthCoordinate, \
    FlatEarthToGPSCoordinateTransformation, GPSCoordinate
from flockwave.server.model.uav import UAVBase, UAVDriver
from math import cos, sin, pi
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
        uav.center = center
        uav.radius = radius
        return uav


class FakeUAVState(Enum):
    """Enum class that represents the possible states of a fake UAV."""

    LANDED = 0
    TAKEOFF = 1
    AIRBORNE = 2
    LANDING = 3


class FakeUAV(UAVBase):
    """Model object representing a fake UAV provided by this extension."""

    def __init__(self, *args, **kwds):
        super(FakeUAV, self).__init__(*args, **kwds)

        self._center = None
        self._pos_flat = FlatEarthCoordinate(x=0, y=0)
        self._state = None
        self._trans = None
        self._transition_progress = 0.0

        self.angle = 0.0
        self.radius = 0.0
        self.landing_radius = 0.0
        self.angular_velocity = 0.0
        self.state = FakeUAVState.TAKEOFF
        self.step(0)

    @property
    def center(self):
        """Returns the point around which the UAV is circling."""
        return self._center

    @center.setter
    def center(self, value):
        self._center = value.copy()
        self._pos_flat.alt = self._center.alt.copy()
        self._trans = FlatEarthToGPSCoordinateTransformation(
            origin=self._center)

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

    def step(self, dt):
        """Simulates a single step of the trajectory of the fake UAV based
        on its state and the amount of time that has passed.

        Parameters:
            dt (float): the time that has passed, in seconds.
        """
        if self._trans is None:
            # We don't have a center for the circle yet so do nothing
            return

        state = self._state

        # Update the angle
        if state != FakeUAVState.LANDED:
            # When not landed, the UAV is circling in the air with a
            # prescribed angular velocity. Otherwise, the angle does
            # not change.
            self.angle += self.angular_velocity * dt

        # Calculate the radius and altitude
        altitude, radius = self._center.alt.copy(), self.radius
        if state in (FakeUAVState.LANDING, FakeUAVState.TAKEOFF):
            # During landing or takeoff, the transition progress variable
            # is increased from zero to one in a three-second interval, and
            # the altitude and radius is adjusted according to an easing
            # function.
            remaining_progress = 1.0 - self._transition_progress
            progress_increase = dt / 3
            if remaining_progress < progress_increase:
                self._transition_progress = 1.0
                dt -= remaining_progress
            else:
                self._transition_progress += progress_increase
                dt = 0

            eased_progress = self._transition_progress
            if state == FakeUAVState.LANDING:
                eased_progress = 1 - eased_progress

            altitude.value = eased_progress * altitude.value
            radius = radius * (0.5 + eased_progress * 0.5)

            if self._transition_progress >= 1:
                # Transition finished.
                if state == FakeUAVState.LANDING:
                    self.state = FakeUAVState.LANDED
                else:
                    self.state = FakeUAVState.AIRBORNE

        x = cos(self.angle) * radius
        y = sin(self.angle) * radius
        self._pos_flat.update(x=x, y=y, alt=altitude)
        self.update_status(position=self._trans.to_gps(self._pos_flat))


class FakeUAVProviderExtension(ExtensionBase):
    """Extension that creates one or more fake UAVs in the server."""

    def __init__(self):
        """Constructor."""
        super(FakeUAVProviderExtension, self).__init__()
        self.uavs = []
        self.uav_ids = []
        self._driver = FakeUAVDriver()
        self._status_updater = StatusUpdater(self)

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
        beta = 2 * pi / configuration.get("time_of_single_cycle", 10)

        self.uav_ids = [id_format.format(index) for index in xrange(count)]
        self.uavs = [
            self._driver.create_uav(id, center=center, radius=radius,
                                    angle=2 * pi / count * index,
                                    angular_velocity=beta)
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
