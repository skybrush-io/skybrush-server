from __future__ import annotations

from typing import Iterator, TYPE_CHECKING

from flockwave.server.ext.show.takeoff import (
    ScheduledTakeoffManager,
    TakeoffConfiguration,
)

from .packets import create_start_time_configuration_packet

__all__ = ("ScheduledTakeoffManager",)

if TYPE_CHECKING:
    from .driver import MAVLinkUAV
    from .network import MAVLinkNetwork


class MAVLinkScheduledTakeoffManager(ScheduledTakeoffManager["MAVLinkUAV"]):
    """Class that manages the automatic takeoff process on a single MAVLink
    network.
    """

    _network: MAVLinkNetwork
    """The MAVLink network that owns this scheduled takeoff manager."""

    def __init__(self, network: MAVLinkNetwork):
        """Constructor.

        Parameters:
            network: the network whose automatic takeoff process this object
                manages
        """
        super().__init__(log=network.log)
        self._network = network

    async def broadcast_takeoff_configuration(
        self, config: TakeoffConfiguration
    ) -> None:
        packet = create_start_time_configuration_packet(
            start_time=config.takeoff_time,
            authorization_scope=config.authorization_scope,
            should_update_takeoff_time=config.should_update_takeoff_time,
        )
        await self._network.broadcast_packet(packet)

    def iter_uavs_to_schedule(self) -> Iterator[MAVLinkUAV]:
        """Returns an iterator over the UAVs managed by this object that are
        to be updated on an individual basis if they do not receive the
        broadcast configuration packet or do not respond to it.

        May return an empty iterator if you do not want to support individual
        configuration for the UAVs.
        """
        return (
            uav
            for uav in self._network.uavs()
            if uav.is_connected and uav.supports_scheduled_takeoff
        )

    def uav_needs_update(self, uav: MAVLinkUAV, config: TakeoffConfiguration) -> bool:
        """Returns whether the given UAV needs to be updated if the desired
        takeoff configuration is the one provided as `config`.

        May return False unconditionally if you do not want to support individual
        configuration for the UAVs.

        Args:
            uav: the UAV to check
            config: the desired takeoff configuration to check against
        """
        if config.authorization_scope != uav.scheduled_takeoff_authorization_scope:
            # Auth scope is different so we definitely need an update
            return True

        if config.should_update_takeoff_time:
            # Takeoff time must be cleared (None) or set to a specific
            # value; we need an update if it is different from what
            # we have on the UAV
            return uav.scheduled_takeoff_time != config.takeoff_time

        # Auth scope is the same and the takeoff time does not
        # need to change
        return False

    async def update_uav(self, uav: MAVLinkUAV, config: TakeoffConfiguration) -> None:
        desired_auth_scope = config.authorization_scope
        desired_takeoff_time = config.takeoff_time_in_legacy_format

        if (
            desired_takeoff_time is None or desired_takeoff_time >= 0
        ) and desired_takeoff_time != uav.scheduled_takeoff_time:
            await uav.set_scheduled_takeoff_time(seconds=desired_takeoff_time)

        if desired_auth_scope != uav.scheduled_takeoff_authorization_scope:
            await uav.set_authorization_scope(desired_auth_scope)
