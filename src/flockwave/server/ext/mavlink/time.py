from __future__ import annotations

from contextlib import contextmanager
from logging import Logger
from typing import TYPE_CHECKING, Iterator

from blinker import Signal

from flockwave.server.ext.show.time import (
    BinaryTimeAxisConfiguration,
    TimeAxisConfigurationManager,
)
from flockwave.server.ext.signals import SignalsExtensionAPI

from .channel import Channel
from .packets import create_time_axis_configuration_packet

__all__ = (
    "MAVLinkTimeAxisConfigurationManager",
    "TimeAxisConfigurationSignalDispatcher",
)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


class MAVLinkTimeAxisConfigurationManager(TimeAxisConfigurationManager):
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
        try:
            spec = create_time_axis_configuration_packet(config)
        except ValueError as ex:
            if self._log:
                self._log.warning(f"Could not create time axis config packet: {ex}")
            raise

        await self._network.broadcast_packet(spec, channel=Channel.SHOW_CONTROL)


class TimeAxisConfigurationSignalDispatcher(TimeAxisConfigurationManager):
    """Class that dispatches a signal via the signals API whenever a time axis
    configuration object is acted upon by the MAVLink networks.
    """

    _signal: Signal | None = None
    """The signal to dispatch when a time axis configuration object is acted upon by
    the MAVLink networks. `None` to do nothing.

    The signal must take a single keyword argument named `spec` that contains the
    MAVLink message specification for the packet that we need to send to all the drones
    in order to update the time axis configuration.
    """

    async def broadcast_time_axis_configuration(
        self, config: BinaryTimeAxisConfiguration
    ) -> None:
        if not self._signal:
            return

        try:
            spec = create_time_axis_configuration_packet(config)
        except ValueError as ex:
            if self._log:
                self._log.warning(f"Could not create time axis config packet: {ex}")
            raise

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
