"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from __future__ import absolute_import

from .base import ExtensionBase
from eventlet.greenthread import sleep, spawn
from flockwave.server.model import UAVStatusInfo

__all__ = ()


class FakeUAVProviderExtension(ExtensionBase):
    """Extension that creates one or more fake UAVs in the server."""

    def __init__(self):
        """Constructor."""
        super(FakeUAVProviderExtension, self).__init__()
        self.uavs = []
        self._status_reporter = StatusReporter(self)

    def configure(self, configuration):
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "FAKE-{0}")
        self._status_reporter.delay = configuration.get("delay", 1)

        self.uavs = [id_format.format(index) for index in xrange(count)]
        for uav_id in self.uavs:
            self.app.uav_registry.update_uav_status(uav_id, None)

    def spindown(self):
        self._status_reporter.stop()

    def spinup(self):
        self._status_reporter.start()


class StatusReporter(object):
    """Status reporter object that manages a green thread that will report
    the status of the fake UAVs periodically.
    """

    def __init__(self, ext, delay=1):
        """Constructor."""
        self._thread = None
        self._delay = None
        self.delay = delay
        self.ext = ext

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
        with self.ext.app.app_context():
            while True:
                body = {
                    "type": "UAV-INF"
                }
                message = hub.create_notification(body=body)
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
        self._thread = spawn(self.report_status)

    def stop(self):
        """Stops the status reporter thread if it is running."""
        if not self.running:
            return
        self._thread.kill()
        self._thread = None


construct = FakeUAVProviderExtension
