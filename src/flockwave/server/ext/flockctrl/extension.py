"""Flockwave server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from __future__ import absolute_import

from contextlib import ExitStack
from datetime import datetime
from functools import partial
from pytz import utc
from trio_util import wait_all
from typing import Any, Dict, Optional, Tuple

from flockwave.connections import Connection, create_connection
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose
from flockwave.server.networking import format_socket_address
from flockwave.server.utils import datetime_to_unix_timestamp

from .driver import FlockCtrlDriver
from .packets import FlockCtrlClockSynchronizationPacket
from .wireless import WirelessCommunicationManager

__all__ = ("construct", "dependencies")


def create_wireless_connection_configuration_for_subnet(
    subnet: str, port: int = 4243
) -> Dict[str, Any]:
    """Creates a configuration file snippet that configures the wireless
    connection to the given IPv4 subnet.

    Parameters:
        subnet: the IPv4 subnet in slashed notation
        port: the port to listen on for incoming broadcast messages
    """
    return {
        "broadcast": f"udp-broadcast:{subnet}?port={port}",
        "unicast": f"udp-subnet:{subnet}",
    }


#: Dictionary that resolves common preset aliases used in the configuration file
PRESETS = {
    "default": create_wireless_connection_configuration_for_subnet("10.0.0.0/8"),
    "local": {
        "broadcast": "udp-multicast://239.255.67.77:4243?interface=127.0.0.1",
        "unicast": "udp://127.0.0.1",
    },
}


class FlockCtrlDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def __init__(self):
        super(FlockCtrlDronesExtension, self).__init__()
        self._driver = None

        self._wireless_communicator = WirelessCommunicationManager(self)
        self._wireless_communicator.on_packet.connect(
            self._handle_inbound_packet, sender=self._wireless_communicator
        )

    def _create_connections(
        self, configuration
    ) -> Tuple[Optional[Connection], Optional[Connection]]:
        """Creates the wireless broadcast and unicast link objects from the
        configuration object of the extension.

        Parameters:
            configuration: the configuration object of the extension

        Returns:
            the broadcast and the unicast link; either of them may be
            `None` if they are not configured
        """
        connection_config = configuration.get("connections", {})
        wireless_config = connection_config.get("wireless", {})

        if isinstance(wireless_config, str):
            if "/" in wireless_config:
                # Probably an IPv4 network in slashed notation
                wireless_config = create_wireless_connection_configuration_for_subnet(
                    wireless_config
                )
            else:
                preset = PRESETS.get(wireless_config)
                if preset:
                    wireless_config = preset
                else:
                    raise KeyError(f"no such configuration preset: {wireless_config}")

        broadcast_link = self._create_lowlevel_connection(
            wireless_config.get("broadcast")
        )
        unicast_link = self._create_lowlevel_connection(wireless_config.get("unicast"))

        return broadcast_link, unicast_link

    def _create_driver(self):
        return FlockCtrlDriver()

    async def run(self, app, configuration):
        broadcast_link, unicast_link = self._create_connections(configuration)

        clock_registry = app.import_api("clocks").registry
        with ExitStack() as stack:
            # Attach ourselves to the clock registry
            stack.enter_context(
                clock_registry.clock_changed.connected_to(
                    self._on_clock_changed, sender=clock_registry
                )
            )

            # Register the broadcast link
            stack.enter_context(
                app.connection_registry.use(
                    broadcast_link,
                    "Wireless",
                    description="Upstream wireless connection",
                    purpose=ConnectionPurpose.uavRadioLink,
                )
            )

            await unicast_link.open()

            # Start the links
            await wait_all(
                partial(app.supervise, broadcast_link, task=self._run_broadcast_link),
                partial(app.supervise, unicast_link, task=self._run_unicast_link),
            )

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
        driver.create_device_tree_mutator = self.create_device_tree_mutation_context
        driver.send_packet = self.send_packet

    def send_packet(self, packet, destination=None):
        """Requests the extension to send the given FlockCtrl packet to the
        given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (Optional[bytes]): the long destination address to
                send the packet to. ``None`` means to send a broadcast
                packet.
        """
        medium, address = destination
        if medium == "wireless":
            comm = self._wireless_communicator
        else:
            raise ValueError("unknown medium: {0!r}".format(medium))
        comm.send_packet(packet, address)

    def _create_lowlevel_connection(
        self, specifier: Optional[str]
    ) -> Optional[Connection]:
        """Create a low-level wireless connection object from the given
        connection specifier parsed from the extension configuration.

        Parameters:
            specifier: the connection specifier URL that tells the extension how
                to construct the connection object. ``None`` means that no
                connection should be constructed.

        Returns:
            Connection: the constructed low-level connection object or ``None``
            if the specifier was ``None``
        """
        return create_connection(specifier) if specifier else None

    def _handle_inbound_packet(self, sender, packet):
        """Handles an inbound data packet from a communication link."""
        self._driver.handle_inbound_packet(packet)

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
            sequence_id=0,  # TODO(ntamas)
            clock_id=5,  # MIDI timecode clock in FlockCtrl
            running=clock.running,
            local_timestamp=now,
            ticks=clock.ticks_given_time(now_as_timestamp),
            ticks_per_second=clock.ticks_per_second,
        )
        self.send_packet(packet)

    async def _run_broadcast_link(self, link: Connection) -> None:
        """Background task that handles the broadcast link of the extension."""
        address = format_socket_address(link.address)
        self.log.info(f"Listening for incoming flockctrl packets on {address}")
        try:
            while True:
                print("BC", await link.read())
        finally:
            self.log.info(
                f"Stopped listening for incoming flockctrl packets on {address}"
            )

    async def _run_unicast_link(self, link: Connection) -> None:
        """Background task that handles the unicast link of the extension."""
        address = format_socket_address(link.address)
        self.log.info(f"Sending packets on {address}")
        try:
            while True:
                print(await link.read())
        finally:
            self.log.info(f"Stopped sending flockctrl packets on {address}")


construct = FlockCtrlDronesExtension
dependencies = ("clocks",)
