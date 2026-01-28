from __future__ import annotations

from typing import TYPE_CHECKING

from flockwave.server.ext.show.time import (
    BinaryTimeAxisConfiguration,
    TimeAxisConfigurationManager,
)

from .packets import create_time_axis_configuration_packet

__all__ = ("MAVLinkTimeAxisConfigurationManager",)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


class MAVLinkTimeAxisConfigurationManager(TimeAxisConfigurationManager["MAVLinkUAV"]):
    """Class that manages the time axis configuration updates on a single MAVLink
    network.
    """

    _network: MAVLinkNetwork
    """The MAVLink network that owns this time axis configuration manager."""

    def __init__(self, network: MAVLinkNetwork):
        """Constructor.

        Parameters:
            network: the network whose time axis configuration process this object
                manages
        """
        super().__init__(log=network.log)
        self._network = network

    async def broadcast_time_axis_configuration(
        self, config: BinaryTimeAxisConfiguration
    ) -> None:
        packet = create_time_axis_configuration_packet(config)
        await self._network.broadcast_packet(packet)
