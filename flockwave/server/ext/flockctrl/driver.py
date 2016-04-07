"""Driver class for FlockCtrl-based drones."""

from flockwave.server.ext.logger import log
from flockwave.server.model.uav import UAVBase, UAVDriver

from .packets import FlockCtrlStatusPacket

__all__ = ("FlockCtrlDriver", )


class FlockCtrlDriver(UAVDriver):
    """Driver class for FlockCtrl-based drones."""

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (FlockwaveServer): the app in which the driver lives
            id_format (str): Python format string that receives a numeric
                drone ID in the flock and returns its preferred formatted
                identifier that is used when the drone is registered in the
                server, or any other object that has a ``format()`` method
                accepting a single integer as an argument and returning the
                preferred UAV identifier.
        """
        super(FlockCtrlDriver, self).__init__()
        self._packet_handlers = self._configure_packet_handlers()
        self.app = app
        self.id_format = id_format
        self.log = log.getChild("flockctrl").getChild("driver")

    def _configure_packet_handlers(self):
        """Constructs a mapping that maps FlockCtrl packet types to the
        handler functions that should be responsible for handling them.
        """
        return {
            FlockCtrlStatusPacket: self._handle_inbound_status_packet
        }

    def _create_uav(self, formatted_id):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            formatted_id (str): the formatted string identifier of the UAV
                to create

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        uav = FlockCtrlUAV(formatted_id, driver=self)
        return uav

    def _get_or_create_uav(self, id):
        """Retrieves the UAV with the given numeric ID, or creates one if
        the driver has not seen a UAV with the given ID yet.

        Parameters:
            id (int): the numeric identifier of the UAV to retrieve

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        formatted_id = self.id_format.format(id)
        uav_registry = self.app.uav_registry
        if not uav_registry.contains(formatted_id):
            uav = self._create_uav(formatted_id)
            uav_registry.add(uav)
        return uav_registry.find_by_id(formatted_id)

    def handle_inbound_packet(self, packet):
        """Handles an inbound FlockCtrl packet received over an XBee
        connection.
        """
        packet_class = packet.__class__
        handler = self._packet_handlers.get(packet_class)
        if handler is None:
            self.log.warn("No packet handler defined for packet "
                          "class: {0}".format(packet_class.__name__))
        else:
            handler(packet)

    def _handle_inbound_status_packet(self, packet):
        """Handles an inbound FlockCtrl status packet.

        Parameters:
            packet (FlockCtrlStatusPacket): the packet to handle
        """
        uav = self._get_or_create_uav(packet.id)
        uav.update_status(
            position=packet.location,
            velocity=packet.velocity,
            heading=packet.heading
        )
        # TODO: rate limiting
        message = self.app.create_UAV_INF_message_for([uav.id])
        self.app.message_hub.send_message(message)


class FlockCtrlUAV(UAVBase):
    """Subclass for UAVs created by the driver for FlockCtrl-based
    drones.
    """

    pass
