from __future__ import annotations

from contextlib import contextmanager
from logging import Logger
from struct import Struct
from typing import TYPE_CHECKING, Iterator

from blinker import Signal

from flockwave.server.ext.signals import SignalsExtensionAPI
from flockwave.server.tasks.led_lights import (
    LEDLightConfigurationManagerBase,
    LightConfiguration,
    LightEffectType,
)

from .channel import Channel
from .packets import create_led_control_packet
from .types import MAVLinkMessageSpecification

__all__ = (
    "MAVLinkLEDLightConfigurationManager",
    "LEDLightConfigurationSignalDispatcher",
)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


_light_control_packet_struct = Struct("<BBBHB")


def create_light_control_packet(
    config: LightConfiguration,
) -> MAVLinkMessageSpecification | None:
    """Creates a MAVLink message specification for the MAVLink message that
    we need to send to all the drones in order to instruct them to do the
    current light effect.
    """
    is_active = config.effect == LightEffectType.SOLID
    data = _light_control_packet_struct.pack(
        config.color[0],
        config.color[1],
        config.color[2],
        (
            30000  # drone will switch back to normal mode after 30 sec
            if is_active
            else 0
        ),  # submitting zero duration turns off any effect that we have
        1 if is_active else 0,
    )
    return create_led_control_packet(data, broadcast=True)


class MAVLinkLEDLightConfigurationManager(
    LEDLightConfigurationManagerBase[MAVLinkMessageSpecification]
):
    """Class that manages the state of the LED lights on the drones when they
    are controlled by commands from the GCS.
    """

    def __init__(self, network: "MAVLinkNetwork"):
        """Constructor.

        Parameters:
            network: the network whose LED lights this object manages
        """
        super().__init__()
        self._network: "MAVLinkNetwork" = network

    def _create_light_control_packet(
        self, config: LightConfiguration
    ) -> MAVLinkMessageSpecification | None:
        """Creates a MAVLink message specification for the MAVLink message that
        we need to send to all the drones in order to instruct them to do the
        current light effect.
        """
        return create_light_control_packet(config)

    def _get_logger(self) -> Logger | None:
        return self._network.log if self._network else None

    async def _send_light_control_packet(
        self, packet: MAVLinkMessageSpecification
    ) -> None:
        return await self._network.broadcast_packet(
            packet, channel=Channel.SHOW_CONTROL
        )


class LEDLightConfigurationSignalDispatcher(
    LEDLightConfigurationManagerBase[MAVLinkMessageSpecification]
):
    """Class that dispatches a signal via the signals API whenever the state of the LED
    lights on the drones are controlled by commands from the GCS.
    """

    _log: Logger | None = None
    """The logger to use for logging messages related to the LED light configuration
    management. `None` to do no logging.
    """

    _signal: Signal | None = None
    """The signal to dispatch when the state of the LED lights on the drones are
    controlled by commands from the GCS. `None` to do nothing.

    The signal must take a single keyword argument named `spec` that contains the
    MAVLink message specification for the packet that we need to send to all the drones
    in order to update the LED lights.
    """

    def _create_light_control_packet(
        self, config: LightConfiguration
    ) -> MAVLinkMessageSpecification | None:
        """Creates a MAVLink message specification for the MAVLink message that
        we need to send to all the drones in order to instruct them to do the
        current light effect.
        """
        return create_light_control_packet(config)

    def _get_logger(self) -> Logger | None:
        return self._log

    async def _send_light_control_packet(
        self, packet: MAVLinkMessageSpecification
    ) -> None:
        if self._signal:
            self._signal.send(self, spec=packet)

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
