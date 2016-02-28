"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from __future__ import absolute_import, division

from .base import ExtensionBase
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

    def create_uav(self, id):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id (str): the identifier of the UAV to create

        Returns:
            FakeUAV: an appropriate fake UAV object
        """
        return FakeUAV(id, driver=self)


class FakeUAV(UAVBase):
    """Model object representing a fake UAV provided by this extension."""

    pass


class FakeUAVProviderExtension(ExtensionBase):
    """Extension that creates one or more fake UAVs in the server."""

    def __init__(self):
        """Constructor."""
        super(FakeUAVProviderExtension, self).__init__()
        self.center = None
        self.uavs = []
        self.uav_ids = []
        self._driver = FakeUAVDriver()
        self._status_reporter = StatusReporter(self)

    def configure(self, configuration):
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "FAKE-{0}")
        self._status_reporter.delay = configuration.get("delay", 1)

        center = configuration.get("center")
        self.center = GPSCoordinate(
            lat=center["lat"], lon=center["lon"],
            alt=Altitude.relative_to_home(center["alt"])
        )
        self.radius = float(configuration.get("radius", 10))
        self.time_of_single_cycle = float(
            configuration.get("time_of_single_cycle", 10)
        )

        self.uav_ids = [id_format.format(index) for index in xrange(count)]
        self.uavs = [self._driver.create_uav(id) for id in self.uav_ids]
        for uav in self.uavs:
            uav.update_status(position=self.center)
            self.app.uav_registry.add(uav)

    def spindown(self):
        self._status_reporter.stop()

    def spinup(self):
        self._status_reporter.start()


# TODO: StatusReporter is not really nice; basically all it does requires
# it to reach out to self.ext

class StatusReporter(object):
    """Status reporter object that manages a green thread that will report
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

    def _update_uav_statuses(self):
        """Updates the status of all the UAVs managed by this extension."""
        radius = self.ext.radius
        trans = FlatEarthToGPSCoordinateTransformation(origin=self.ext.center)
        flat_coords = FlatEarthCoordinate()

        num_uavs = len(self.ext.uavs)
        dt = time() - self._started_at
        full_circle = 2 * pi
        angle_base_rad = dt * full_circle / self.ext.time_of_single_cycle

        for index, uav in enumerate(self.ext.uavs):
            # Calculate the angle of the UAV
            angle_rad = angle_base_rad + index * full_circle / num_uavs

            # Calculate the coordinates of the UAV in flat Earth
            flat_coords.update(
                x=cos(angle_rad) * radius,
                y=sin(angle_rad) * radius
            )

            # Recalculate the position of the UAV in lat-lon
            position = trans.to_gps(flat_coords)

            # Update the status of the UAV
            uav.update_status(position=position)

    @property
    def delay(self):
        """Number of seconds that must pass between two consecutive
        simulated status updates to the UAVs.
        """
        return self._delay

    @delay.setter
    def delay(self, value):
        self._delay = max(float(value), 0)

    def report_status(self):
        """Reports the status of all the UAVs to the UAV registry."""
        hub = self.ext.app.message_hub
        while not self.stopping:
            self._update_uav_statuses()
            message = self._create_status_notification()
            hub.send_message(message)
            sleep(self._delay)

    @property
    def running(self):
        """Returns whether the status reporter thread is running."""
        return self._thread is not None

    def start(self):
        """Starts the status reporter thread if it is not running yet."""
        if self.running:
            return

        status_reporter = copy_current_request_context(self.report_status)
        self.stopping = False
        self._thread = spawn(status_reporter)
        self._thread.link(self._on_thread_stopped)

    def stop(self):
        """Stops the status reporter thread if it is running."""
        if not self.running:
            return

        self.stopping = True

    def _on_thread_stopped(self, thread):
        """Handler called when the status reporter thread stops."""
        self.stopping = False
        self._thread = None


construct = FakeUAVProviderExtension
