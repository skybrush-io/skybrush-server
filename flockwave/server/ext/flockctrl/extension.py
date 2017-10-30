"""Flockwave server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from __future__ import absolute_import

from datetime import datetime
from pytz import utc
from xbee import ZigBee

from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose
from flockwave.server.utils import datetime_to_unix_timestamp

from .driver import FlockCtrlDriver
from .packets import FlockCtrlClockSynchronizationPacket
from .wireless import WirelessCommunicationManager
from .xbee import XBeeCommunicationManager

__all__ = ("construct", )


class FlockCtrlDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def __init__(self):
        super(FlockCtrlDronesExtension, self).__init__()
        self._driver = None

        self._wireless_lowlevel_link = None
        self._wireless_communicator = WirelessCommunicationManager(self)
        self._wireless_communicator.on_packet.connect(
            self._handle_inbound_packet,
            sender=self._wireless_communicator
        )

        self._xbee_lowlevel_link = None
        self._xbee_communicator = XBeeCommunicationManager(self)
        self._xbee_communicator.on_packet.connect(
            self._handle_inbound_packet,
            sender=self._xbee_communicator
        )

    def _create_driver(self):
        return FlockCtrlDriver()

    def configure(self, configuration):
        connection_config = configuration.get("connections", {})
        self.xbee_lowlevel_link = self._configure_lowlevel_connection(
            connection_config.get("xbee"))
        self.wireless_lowlevel_link = self._configure_lowlevel_connection(
            connection_config.get("wireless"))
        super(FlockCtrlDronesExtension, self).configure(configuration)

    def on_app_changed(self, old_app, new_app):
        super(FlockCtrlDronesExtension, self).on_app_changed(old_app, new_app)

        if old_app is not None:
            old_app.clock_registry.clock_changed.disconnect(
                self._on_clock_changed, sender=old_app.clock_registry)

        if new_app is not None:
            new_app.clock_registry.clock_changed.connect(
                self._on_clock_changed, sender=new_app.clock_registry)

    def unload(self):
        self.wireless_lowlevel_link = None
        self.xbee_lowlevel_link = None

    @property
    def wireless_lowlevel_link(self):
        return self._wireless_lowlevel_link

    @wireless_lowlevel_link.setter
    def wireless_lowlevel_link(self, value):
        if self._wireless_lowlevel_link is not None:
            self._wireless_communicator.connection = None
            self._wireless_lowlevel_link.close()
            self.app.connection_registry.remove("Wireless")

        self._wireless_lowlevel_link = value

        if self._wireless_lowlevel_link is not None:
            self.app.connection_registry.add(
                self._wireless_lowlevel_link, "Wireless",
                description="Upstream wireless connection",
                purpose=ConnectionPurpose.uavRadioLink
            )

            self._wireless_lowlevel_link.open()
            self._wireless_communicator.connection = \
                self._wireless_lowlevel_link

    @property
    def xbee_lowlevel_link(self):
        return self._xbee_lowlevel_link

    @xbee_lowlevel_link.setter
    def xbee_lowlevel_link(self, value):
        if self._xbee_lowlevel_link is not None:
            self._xbee_communicator.xbee = None
            self._xbee_lowlevel_link.close()
            self.app.connection_registry.remove("XBee")

        self._xbee_lowlevel_link = value

        if self._xbee_lowlevel_link is not None:
            self.app.connection_registry.add(
                self._xbee_lowlevel_link, "XBee",
                description="Upstream XBee connection",
                purpose=ConnectionPurpose.uavRadioLink
            )

            self._xbee_lowlevel_link.open()
            self._xbee_communicator.xbee = ZigBee(self._xbee_lowlevel_link)

    def _configure_lowlevel_connection(self, specifier):
        """Configures the low-level XBee or wireless connection object from
        the given connection specifier parsed from the extension
        configuration.

        Parameters:
            specifier (Optional[str]): the connection specifier URL that
                tells the extension how to find the serial port to which the
                XBee is connected, or how to find the subnet on which the
                wireless status packets are broadcast. ``None`` means that
                no connection should be constructed.

        Returns:
            Optional[Connection]: the constructed low-level connection object
                or ``None`` if the specifier was ``None``
        """
        if specifier:
            return reconnecting(create_connection(specifier))
        else:
            return None

    def configure_driver(self, driver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver (UAVDriver): the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        driver.id_format = configuration.get("id_format", "{0:02}")
        driver.log = self.log.getChild("driver")
        driver.create_device_tree_mutator = \
            self.create_device_tree_mutation_context
        driver.send_packet = self.send_packet

    def _handle_inbound_packet(self, sender, packet):
        """Handles an inbound data packet from an XBee or wireless link."""
        self._driver.handle_inbound_packet(packet)

    def send_packet(self, packet, destination=None):
        """Requests the extension to send the given FlockCtrl packet to the
        given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (Optional[bytes]): the long destination address to
                send the packet to. ``None`` means to send a broadcast
                packet.
        """
        # TODO(ntamas): currently we always send packets via the XBee link. We
        # should add support for the wifi link as well
        medium, address = destination
        if medium == "wireless":
            comm = self._wireless_communicator
        elif medium == "xbee":
            comm = self._xbee_communicator
        else:
            raise ValueError("unknown medium: {0!r}".format(medium))
        comm.send_packet(packet, address)

    def _on_clock_changed(self, sender, clock):
        """Handler that is called when one of the clocks changed in the
        server application.

        FlockCtrl drones are interested in the MIDI clock only, therefore
        we only send a clock synchronization message to the drones if the
        clock that changed has ID = ``mtc``.
        """
        if clock.id != "mtc":
            return

        now = datetime.now(utc)
        now_as_timestamp = datetime_to_unix_timestamp(now)
        packet = FlockCtrlClockSynchronizationPacket(
            sequence_id=0,      # TODO(ntamas)
            clock_id=5,         # MIDI timecode clock in FlockCtrl
            running=clock.running,
            local_timestamp=now,
            ticks=clock.ticks_given_time(now_as_timestamp),
            ticks_per_second=clock.ticks_per_second
        )
        self.send_packet(packet)


construct = FlockCtrlDronesExtension
