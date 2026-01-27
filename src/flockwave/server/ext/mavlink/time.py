from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from flockwave.server.ext.show.time import (
    BinaryTimeAxisConfiguration,
    TimeAxisConfigurationManager,
)

from .packets import create_time_axis_configuration_packet

__all__ = ("MAVLinkTimeAxisConfigurationManager",)

if TYPE_CHECKING:
    from .driver import MAVLinkUAV
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

    def iter_uavs_to_schedule(self) -> Iterator[MAVLinkUAV]:
        """Returns an iterator over the UAVs managed by this object that are
        to be updated on an individual basis if they do not receive the
        broadcast configuration packet or do not respond to it.

        May return an empty iterator if you do not want to support individual
        configuration for the UAVs.
        """
        # TODO: how to check if someone did not get the new config by the broadcast
        # and needs individual addressing? (We return empty list for the time being)
        return (uav for uav in self._network.uavs() if uav.is_connected and False)

    def uav_needs_update(
        self, uav: MAVLinkUAV, config: BinaryTimeAxisConfiguration
    ) -> bool:
        """Returns whether the given UAV needs to be updated if the desired
        time axis configuration is the one provided as `config`.

        May return False unconditionally if you do not want to support individual
        configuration for the UAVs.

        Args:
            uav: the UAV to check
            config: the desired time axis configuration to check against
        """
        # TODO: how to check if someone did not get the new config by the broadcast
        # and needs individual addressing? (We return empty list for the time being)
        return False

    async def update_uav(
        self, uav: MAVLinkUAV, config: BinaryTimeAxisConfiguration
    ) -> None:
        # TODO: implement individual message transfer

        # if ...:
        #     await uav.update_time_axis_configuration(config)

        pass
