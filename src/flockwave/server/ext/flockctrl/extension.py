"""Skybrush server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from __future__ import absolute_import

from contextlib import ExitStack
from datetime import datetime, timezone
from functools import partial
from trio.abc import ReceiveChannel
from typing import Any, Dict, Optional, Tuple

from flockwave.connections import Connection, create_connection, IPAddressAndPort
from flockwave.protocols.flockctrl.packets import (
    FlockCtrlPacket,
    ClockSynchronizationPacket,
    RawGPSInjectionPacket,
)
from flockwave.server.comm import BROADCAST, CommunicationManager
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose
from flockwave.server.utils import datetime_to_unix_timestamp

from flockwave.server.ext.show.config import LightConfiguration

from .comm import create_communication_manager
from .driver import FlockCtrlDriver
from .led_lights import FlockCtrlLEDLightConfigurationManager

# from .wireless import WirelessCommunicationManager

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
        "broadcast": f"udp-broadcast-in:{subnet}?port={port}",
        "unicast": f"udp-subnet:{subnet}?allow_broadcast=1",
    }


#: Dictionary that resolves common wireless connection preset aliases used in
#: the configuration file
WIRELESS_PRESETS = {
    "default": create_wireless_connection_configuration_for_subnet("10.0.0.0/8"),
    "local": {
        "broadcast": "udp-multicast://239.255.67.77:4243?interface=127.0.0.1",
        # ?multicast_interface=127.0.0.1 ensures that multicast packets from
        # this socket are sent on the loopback interface
        "unicast": "udp-listen://127.0.0.1?multicast_interface=127.0.0.1",
    },
}

#: Dictionary that resolves common radio connection preset aliases used in
#: the configuration file
RADIO_PRESETS = {
    "default": "serial:0403:6015?baud=57600"  # SiK radio, FTDI USB serial device
}


class FlockCtrlDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    _driver: Optional[FlockCtrlDriver]
    _comm_manager: Optional[CommunicationManager[FlockCtrlPacket, IPAddressAndPort]]
    _led_manager: Optional[FlockCtrlLEDLightConfigurationManager]

    def __init__(self):
        super(FlockCtrlDronesExtension, self).__init__()

        self._driver = None
        self._comm_manager = None
        self._led_manager = None

    def _create_connections(
        self, configuration
    ) -> Tuple[Optional[Connection], Optional[Connection], Optional[Connection]]:
        """Creates the wireless broadcast and unicast link objects and the
        radio backup link (if any) from the configuration object of the
        extension.

        Parameters:
            configuration: the configuration object of the extension

        Returns:
            the broadcast and the unicast link, and the radio backup link. Any
            of these can be `None` if they are not configured.
        """
        connection_config = configuration.get("connections", {})
        wireless_config = connection_config.get("wireless", {})
        radio_config = connection_config.get("radio", {})

        if isinstance(wireless_config, str):
            if "/" in wireless_config:
                # Probably an IPv4 network in slashed notation
                wireless_config = create_wireless_connection_configuration_for_subnet(
                    wireless_config
                )
            else:
                preset = WIRELESS_PRESETS.get(wireless_config)
                if preset:
                    wireless_config = preset
                else:
                    raise KeyError(
                        f"no such wireless configuration preset: {wireless_config}"
                    )

        if isinstance(radio_config, str) and ":" not in radio_config:
            preset = RADIO_PRESETS.get(radio_config)
            if preset:
                radio_config = preset
            else:
                raise KeyError(f"no such radio configuration preset: {radio_config}")

        broadcast_link = self._create_lowlevel_connection(
            wireless_config.get("broadcast")
        )
        unicast_link = self._create_lowlevel_connection(wireless_config.get("unicast"))
        radio_link = self._create_lowlevel_connection(radio_config)

        # Let the unicast link know where to send broadcast packets
        unicast_link.broadcast_address = broadcast_link.address

        # The radio link also needs a dummy broadcast address; there are not
        # really any addresses in the radio link, but the system needs to have
        # one so it can recognize that the link can broaddcast
        if radio_link:
            radio_link.broadcast_address = ""

        return broadcast_link, unicast_link, radio_link

    def _create_driver(self):
        return FlockCtrlDriver()

    async def run(self, app, configuration):
        broadcast_link, unicast_link, radio_link = self._create_connections(
            configuration
        )

        clock_registry = app.import_api("clocks").registry
        signals = app.import_api("signals")

        with ExitStack() as stack:
            # Attach ourselves to the clock registry
            stack.enter_context(
                clock_registry.clock_changed.connected_to(
                    self._on_clock_changed, sender=clock_registry
                )
            )

            # Register the broadcast link (if any)
            if broadcast_link:
                stack.enter_context(
                    app.connection_registry.use(
                        broadcast_link,
                        "Wireless",
                        description="Upstream wireless connection",
                        purpose=ConnectionPurpose.uavRadioLink,
                    )
                )

            # Register the radio backup link (if any)
            if radio_link:
                stack.enter_context(
                    app.connection_registry.use(
                        radio_link,
                        "Radio",
                        description="Upstream radio connection",
                        purpose=ConnectionPurpose.uavRadioLink,
                    )
                )

            # Create the communication manager
            comm_manager = create_communication_manager()

            # Register the links with the communication manager. The order is
            # important here; the first one will be used for sending, so that
            # must be the unicast link.
            comm_manager.add(unicast_link, name="wireless")
            comm_manager.add(broadcast_link, name="wireless", can_send=False)
            if radio_link is not None:
                comm_manager.add(radio_link, name="radio")

            # Create the LED light configuration manager
            assert self._driver is not None
            led_manager = FlockCtrlLEDLightConfigurationManager(
                self._enqueue_broadcast_packet_over_radio_falling_back_to_wireless
            )

            # Register signal handlers
            stack.enter_context(
                signals.use(
                    {
                        "rtk:packet": self._on_rtk_correction_packet,
                        "show:lights_updated": self._on_show_light_configuration_changed,
                    }
                )
            )

            # Start the communication manager
            try:
                async with self.use_nursery() as nursery:
                    self._comm_manager = comm_manager
                    self._led_manager = led_manager
                    nursery.start_soon(led_manager.run)
                    nursery.start_soon(
                        partial(
                            comm_manager.run,
                            consumer=self._handle_inbound_packets,
                            supervisor=app.supervise,
                            log=self.log,
                        )
                    )
            finally:
                self._comm_manager = None
                self._led_manager = None

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

        driver.broadcast_packet = self._broadcast_packet
        driver.create_device_tree_mutator = self.create_device_tree_mutation_context
        driver.send_packet = self._send_packet
        driver.run_in_background = self.run_in_background

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
            the constructed low-level connection object or ``None`` if the
            specifier was ``None``
        """
        return create_connection(specifier) if specifier else None

    async def _handle_inbound_packets(self, channel: ReceiveChannel):
        """Handles inbound data packets from all the communication links
        that the extension manages.

        Parameters:
            channel: a Trio receive channel that yields inbound data packets.
        """
        async for name, (packet, address) in channel:
            self._driver.handle_inbound_packet(packet, (name, address))

    def _on_clock_changed(self, sender, clock):
        """Handler that is called when one of the clocks changed in the
        server application.

        FlockCtrl drones are interested in the MIDI clock only, therefore
        we only send a clock synchronization message to the drones if the
        clock that changed has ID = ``mtc``.
        """
        if clock.id != "mtc":
            return

        now = datetime.now(timezone.utc)
        now_as_timestamp = datetime_to_unix_timestamp(now)
        packet = ClockSynchronizationPacket(
            sequence_id=0,  # TODO(ntamas)
            clock_id=5,  # MIDI timecode clock in FlockCtrl
            running=clock.running,
            local_timestamp=now,
            ticks=clock.ticks_given_time(now_as_timestamp),
            ticks_per_second=clock.ticks_per_second,
        )
        self._enqueue_broadcast_packet_over_radio_falling_back_to_wireless(packet)

    def _on_show_light_configuration_changed(
        self, sender, config: LightConfiguration
    ) -> None:
        """Handler that is called when the user changes the LED light configuration
        of the drones in the `show` extesion.
        """
        if self._led_manager is None:
            return

        # Make a copy of the configuration in case someone else who comes after
        # us in the handler chain messes with it
        config = config.clone()

        # Send the configuration to the driver to handle it
        self._led_manager.notify_config_changed(config)

    def _on_rtk_correction_packet(self, sender, packet: bytes):
        """Handles an RTK correction packet that the server wishes to forward
        to the drones managed by this extension.

        Parameters:
            packet: the raw RTK correction packet to forward to the drone
        """
        packet_to_inject = RawGPSInjectionPacket(packet)
        self._enqueue_broadcast_packet_over_radio_falling_back_to_wireless(
            packet_to_inject
        )

    async def _broadcast_packet(self, packet: FlockCtrlPacket, medium: str) -> None:
        """Broadcasts a FlockCtrl packet to all UAVs in the network managed
        by the extension.
        """
        await self._send_packet(packet, (medium, BROADCAST))

    def _enqueue_broadcast_packet_over_radio_falling_back_to_wireless(
        self, packet: FlockCtrlPacket
    ) -> None:
        """Enqueues the given packet for a broadcast transmission over the radio
        link, falling back to the wifi link if the radio link is not open.
        """
        if self._comm_manager:
            if self._comm_manager.is_channel_open("radio"):
                self._comm_manager.enqueue_packet(packet, ("radio", BROADCAST))
            if self._comm_manager.is_channel_open("wireless"):
                self._comm_manager.enqueue_packet(packet, ("wireless", BROADCAST))

    async def _send_packet(
        self,
        packet: FlockCtrlPacket,
        destination: Tuple[str, Optional[IPAddressAndPort]],
    ):
        """Requests the extension to send the given FlockCtrl packet to the
        given destination.

        Parameters:
            packet: the packet to send
            destination: the name of the communication channel and the address
                on that communication channel to send the packet to. `None` as
                an address means to send a broadcast packet on the given
                channel.
        """
        if self._comm_manager:
            await self._comm_manager.send_packet(packet, destination)
        else:
            raise ValueError("communication manager not running")


construct = FlockCtrlDronesExtension
dependencies = ("clocks", "signals")
