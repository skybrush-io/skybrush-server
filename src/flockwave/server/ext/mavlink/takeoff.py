from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from logging import Logger
from typing import TYPE_CHECKING, Iterable

from blinker import Signal

from flockwave.server.ext.show.takeoff import (
    ScheduledTakeoffManager,
    SimpleScheduledTakeoffManagerBase,
    TakeoffConfiguration,
)
from flockwave.server.ext.signals import SignalsExtensionAPI

from .channel import Channel
from .packets import create_start_time_configuration_packet

__all__ = ("ScheduledTakeoffManager",)

if TYPE_CHECKING:
    from .driver import MAVLinkUAV
    from .network import MAVLinkNetwork
    from .types import MAVLinkMessageSpecification


def create_mavlink_message_spec_from_takeoff_configuration(
    config: TakeoffConfiguration,
) -> MAVLinkMessageSpecification:
    """Creates a MAVLink message specification for the MAVLink message that we
    need to send to all the drones in order to instruct them to do the
    scheduled takeoff with the given configuration.
    """
    return create_start_time_configuration_packet(
        start_time=config.takeoff_time,
        authorization_scope=config.authorization_scope,
        should_update_takeoff_time=config.should_update_takeoff_time,
    )


class MAVLinkScheduledTakeoffManager(ScheduledTakeoffManager["MAVLinkUAV"]):
    """Class that manages the automatic takeoff process on a single MAVLink
    network.
    """

    _network: MAVLinkNetwork
    """The MAVLink network that owns this scheduled takeoff manager."""

    _use_individual_setup: bool
    """Whether to support individual configuration for the UAVs in the network that do
    not receive the broadcast configuration packet or do not respond to it.
    """

    def __init__(self, network: MAVLinkNetwork, *, use_individual_setup: bool = False):
        """Constructor.

        Parameters:
            network: the network whose automatic takeoff process this object
                manages
            use_individual_setup: whether to support individual configuration for
                the UAVs in the network that do not receive the broadcast
                configuration packet or do not respond to it. The default is `False`;
                this is because the network may contain drones that support the
                `SHOW_START_TIME` parameter only but not `SHOW_START_MSEC`, and
                individual configuration would fail for these drones. You can set the
                `use_individual_setup` parameter to `True` if you are sure that all the
                drones support setting the start time with millisecond precision.
        """
        super().__init__(log=network.log)
        self._network = network
        self._use_individual_setup = bool(use_individual_setup)

    async def broadcast_takeoff_configuration(
        self, config: TakeoffConfiguration
    ) -> None:
        try:
            spec = create_mavlink_message_spec_from_takeoff_configuration(config)
        except ValueError as ex:
            if self._log:
                self._log.warning(f"Could not create start time config packet: {ex}")
            raise

        await self._network.broadcast_packet(spec, channel=Channel.SHOW_CONTROL)

    def iter_uavs_to_schedule(self) -> Iterable[MAVLinkUAV]:
        """Returns an iterator over the UAVs managed by this object that are
        to be updated on an individual basis if they do not receive the
        broadcast configuration packet or do not respond to it.

        May return an empty iterator if you do not want to support individual
        configuration for the UAVs.
        """
        return (
            (
                uav
                for uav in self._network.uavs()
                if uav.is_connected and uav.supports_scheduled_takeoff
            )
            if self._use_individual_setup
            else ()
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
            return uav.scheduled_takeoff_time_msec != config.takeoff_time_msec

        # Auth scope is the same and the takeoff time does not
        # need to change
        return False

    async def update_uav(self, uav: MAVLinkUAV, config: TakeoffConfiguration) -> None:
        desired_auth_scope = config.authorization_scope

        if config.should_update_takeoff_time:
            await uav.set_scheduled_takeoff_time(msec=config.takeoff_time_msec)

        if desired_auth_scope != uav.scheduled_takeoff_authorization_scope:
            await uav.set_authorization_scope(desired_auth_scope)


class ScheduledTakeoffSignalDispatcher(SimpleScheduledTakeoffManagerBase):
    """Class that dispatches a signal via the signals API whenever a scheduled takeoff
    configuration is acted upon by the MAVLink networks.
    """

    _signal: Signal | None = None
    """The signal to dispatch when a scheduled takeoff configuration is acted upon by
    the MAVLink networks. `None` to do nothing.

    The signal must take a single keyword argument named `spec` that contains the
    MAVLink message specification for the packet that we need to send to all the drones
    in order to update the scheduled takeoff time and the authorization state.
    """

    async def broadcast_takeoff_configuration(
        self, config: TakeoffConfiguration
    ) -> None:
        if self._signal:
            spec = create_mavlink_message_spec_from_takeoff_configuration(config)
            self._signal.send(self, spec=spec)

    @contextmanager
    def use(
        self, signals: SignalsExtensionAPI, *, log: Logger | None = None
    ) -> Iterator[None]:
        """Context manager that sets up the signal to dispatch and the logger to use for
        the duration of the context.
        """
        old_signal = self._signal
        self._signal = signals.get("mavlink:show_control")

        old_log = self._log
        self._log = log or old_log

        try:
            yield
        finally:
            self._signal = old_signal
            self._log = old_log
