from __future__ import annotations

from struct import Struct
from typing import Callable, Optional

from flockwave.server.tasks.led_lights import (
    LEDLightConfigurationManagerBase,
    LightConfiguration,
    LightEffectType,
)

from flockwave.server.ext.crazyflie.crtp_extensions import (
    DRONE_SHOW_PORT,
    DroneShowCommand,
)

__all__ = ("CrazyflieLEDLightConfigurationManager",)


_light_control_packet_struct = Struct("<BBBBB")


class CrazyflieLEDLightConfigurationManager(LEDLightConfigurationManagerBase[bytes]):
    """Class that manages the state of the LED lights on the drones when they
    are controlled by commands from the GCS.
    """

    _broadcaster: Callable[[int, bytes], None]

    def __init__(self, broadcaster: Callable[[int, bytes], None]):
        """Constructor."""
        super().__init__()
        self._broadcaster = broadcaster

    def _create_light_control_packet(
        self, config: LightConfiguration
    ) -> Optional[bytes]:
        """Creates a CRTP message payload for the message that we need to
        send to all the drones in order to instruct them to do the current light
        effect.
        """
        return _light_control_packet_struct.pack(
            DroneShowCommand.TRIGGER_GCS_LIGHT_EFFECT,
            1 if config.effect == LightEffectType.SOLID else 0,
            config.color[0],
            config.color[1],
            config.color[2],
        )

    async def _send_light_control_packet(self, packet: bytes) -> None:
        self._broadcaster(DRONE_SHOW_PORT, packet)
