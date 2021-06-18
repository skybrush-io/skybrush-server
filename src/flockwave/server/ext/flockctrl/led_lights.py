from __future__ import annotations

from itertools import cycle
from struct import Struct
from typing import Callable, Optional, Tuple

from flockwave.protocols.flockctrl import FlockCtrlPacket
from flockwave.protocols.flockctrl.enums import MultiTargetCommand
from flockwave.protocols.flockctrl.packets import MultiTargetCommandPacket
from flockwave.server.ext.show.config import (
    LightConfiguration,
    LightEffectType,
    RGBColor,
)
from flockwave.server.tasks.led_lights import LEDLightConfigurationManagerBase

__all__ = ("FlockCtrlLEDLightConfigurationManager",)


_light_control_packet_struct = Struct("<BBBH")


ColorSpec = Tuple[RGBColor, bool]


class FlockCtrlLEDLightConfigurationManager(
    LEDLightConfigurationManagerBase[FlockCtrlPacket]
):
    """Class that manages the state of the LED lights on the drones when they
    are controlled by commands from the GCS.
    """

    _packet: Optional[FlockCtrlPacket]
    _packet_sender: Callable[[FlockCtrlPacket], None]
    _prev_spec: ColorSpec

    def __init__(self, packet_sender: Callable[[FlockCtrlPacket], None]):
        """Constructor."""
        super().__init__()
        self._packet = None
        self._packet_sender = packet_sender
        self._prev_spec = ((0, 0, 0), False)
        self._sequence_id_gen = cycle(range(4))

    def _create_light_control_packet(
        self, config: LightConfiguration
    ) -> Optional[FlockCtrlPacket]:
        """Creates a FlockCtrl message payload for the message that we need to
        send to all the drones in order to instruct them to do the current light
        effect.
        """
        is_active = config.effect == LightEffectType.SOLID
        color = config.color
        spec = color, is_active

        if spec != self._prev_spec:
            # Re-generate the packet with the next sequence ID if either the
            # "active" state or the color changes. Otherwise keep on using the
            # previous packet with the same sequence ID
            self._prev_spec = spec
            payload = _light_control_packet_struct.pack(
                color[0],
                color[1],
                color[2],
                30000  # drone will switch back to normal mode after 30 sec
                if is_active
                else 0,  # submitting zero duration turns off any effect that we have
            )
            self._packet = MultiTargetCommandPacket(
                command=MultiTargetCommand.SET_COLOR,
                sequence_id=next(self._sequence_id_gen),
                payload=payload,
            )

        return self._packet

    async def _send_light_control_packet(self, packet: FlockCtrlPacket) -> None:
        self._packet_sender(packet)
