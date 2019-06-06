"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from flockwave.server.ext.logger import log
from flockwave.server.model.uav import UAVBase, UAVDriver

__all__ = ("MAVLinkDriver",)


class MAVLinkDriver(UAVDriver):
    """Driver class for MAVLink-based drones.

    Attributes:
        app (FlockwaveServer): the app in which the driver lives
        id_format (str): Python format string that receives a numeric
            drone ID in the flock and returns its preferred formatted
            identifier that is used when the drone is registered in the
            server, or any other object that has a ``format()`` method
            accepting a single integer as an argument and returning the
            preferred UAV identifier.
        create_device_tree_mutator (callable): a function that should be
            called by the driver as a context manager whenever it wants to
            mutate the state of the device tree
        send_packet (callable): a function that should be called by the
            driver whenever it wants to send a packet. The function must
            be called with the packet to send, and a pair formed by the
            medium via which the packet should be forwarded and the
            destination address in that medium.
    """

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (FlockwaveServer): the app in which the driver lives
            id_format (str): the format of the UAV IDs used by this driver.
                See the class documentation for more details.
        """
        self._app = None
        super(MAVLinkDriver, self).__init__()

        self.app = app
        self.create_device_tree_mutator = None
        self.id_format = id_format
        self.log = log.getChild("flockctrl").getChild("driver")
        self.send_packet = None

    @property
    def app(self):
        """The app in which the driver lives."""
        return self._app

    @app.setter
    def app(self, value):
        if self._app == value:
            return

        self._app = value


class MAVLinkUAV(UAVBase):
    """Subclass for UAVs created by the driver for MAVLink-based drones.
    """

    def __init__(self, *args, **kwds):
        super(MAVLinkUAV, self).__init__(*args, **kwds)
