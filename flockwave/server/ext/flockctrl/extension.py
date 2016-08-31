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
from .xbee import XBeeCommunicationManager

__all__ = ("construct", )


class FlockCtrlDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def __init__(self):
        super(FlockCtrlDronesExtension, self).__init__()
        self._driver = None
        self._xbee_lowlevel = None
        self._xbee_communicator = XBeeCommunicationManager(self)
        self._xbee_communicator.on_packet.connect(
            self._handle_inbound_xbee_packet,
            sender=self._xbee_communicator
        )

    def _create_driver(self):
        return FlockCtrlDriver()

    def configure(self, configuration):
        self.xbee_lowlevel = self._configure_lowlevel_xbee_connection(
            configuration.get("connection"))
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
        self.xbee_lowlevel = None

    @property
    def xbee_lowlevel(self):
        return self._xbee_lowlevel

    @xbee_lowlevel.setter
    def xbee_lowlevel(self, value):
        if self._xbee_lowlevel is not None:
            self._xbee_communicator.xbee = None
            self._xbee_lowlevel.close()
            self.app.connection_registry.remove("XBee")

        self._xbee_lowlevel = value

        if self._xbee_lowlevel is not None:
            self.app.connection_registry.add(
                self._xbee_lowlevel, "XBee",
                description="Upstream XBee connection for FlockCtrl drones",
                purpose=ConnectionPurpose.uavRadioLink
            )

            self._xbee_lowlevel.open()
            self._xbee = ZigBee(self._xbee_lowlevel)
            self._xbee_communicator.xbee = ZigBee(self._xbee_lowlevel)

    def _configure_lowlevel_xbee_connection(self, specifier):
        """Configures the low-level XBee connection object from the given
        connection specifier parsed from the extension configuration.

        Parameters:
            specifier (str): the connection specifier URL that tells the
                extension how to find the serial port to which the XBee
                is connected

        Returns:
            Connection: an abstract connection that can be used to read and
                write byte-level stuff from/to the XBee
        """
        return reconnecting(create_connection(specifier))

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

    def _handle_inbound_xbee_packet(self, sender, packet):
        """Handles an inbound XBee data packet."""
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
        self._xbee_communicator.send_packet(packet, destination)

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
            sequence_id=0,      # TODO
            clock_id=5,         # MIDI timecode clock in FlockCtrl
            running=clock.running,
            local_timestamp=now,
            ticks=clock.ticks_given_time(now_as_timestamp),
            ticks_per_second=clock.ticks_per_second
        )
        self.send_packet(packet)


construct = FlockCtrlDronesExtension
