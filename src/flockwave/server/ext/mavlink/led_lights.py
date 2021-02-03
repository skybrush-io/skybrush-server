from struct import Struct
from time import monotonic
from trio import BrokenResourceError, Event, move_on_after, sleep
from typing import Optional, TYPE_CHECKING

from flockwave.server.ext.show.config import LightConfiguration, LightEffectType

from .packets import create_led_control_packet
from .types import MAVLinkMessageSpecification

__all__ = ("LEDLightConfigurationManager",)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


_light_control_packet_struct = Struct("<BBBHB")


class LEDLightConfigurationManager:
    """Class that manages the state of the LED lights on the drones when they
    are controlled by commands from the GCS.
    """

    def __init__(self, network: "MAVLinkNetwork"):
        """Constructor.

        Parameters:
            network: the network whose automatic takeoff process this object
                manages
        """
        self._active = False
        self._config = None  # type: Optional[LightConfiguration]
        self._config_last_updated_at = 0
        self._network = network
        self._rapid_mode = False
        self._rapid_mode_triggered = Event()
        self._suppress_warnings_until = 0

    def notify_config_changed(self, config):
        """Notifies the manager that the LED light configuration has changed."""
        # Store the configuration
        self._config = config
        self._config_last_updated_at = monotonic()

        # Note that we need to dispatch messages actively if the mode is not
        # "off"
        self._active = (
            self._config is not None and self._config.effect != LightEffectType.OFF
        )

        # Trigger rapid mode for the next five seconds so we dispatch commands
        # more frequently to ensure that all the drones get it
        self._rapid_mode = True
        self._rapid_mode_triggered.set()

    async def run(self) -> None:
        """Background task that regularly broadcasts messages about the current
        status of the LED configuration of the UAVs.
        """
        log = self._network.log
        while True:
            try:
                await self._run(log)
            except Exception:
                if log:
                    log.exception(
                        "LED light control task stopped unexpectedly, restarting..."
                    )
                await sleep(0.5)

    def _create_light_control_packet(self) -> MAVLinkMessageSpecification:
        """Creates a MAVLink message specification for the MAVLink message that
        we need to send to all the drones in order to instruct them to do the
        current light effect.
        """
        is_active = self._config.effect == LightEffectType.SOLID
        data = _light_control_packet_struct.pack(
            self._config.color[0],
            self._config.color[1],
            self._config.color[2],
            30000  # drone will switch back to normal mode after 30 sec
            if is_active
            else 0,  # submitting zero duration turns off any effect that we have
            1 if is_active else 0,
        )
        return create_led_control_packet(data, broadcast=True)

    async def _run(self, log) -> None:
        while True:
            # Note that we might need to send a packet even if we are inactive
            # to ensure that the drones are informed when the GCS stops sending
            # further LED control commands and switches to "off" mode
            packet = self._create_light_control_packet()
            if packet:
                try:
                    await self._network.broadcast_packet(packet)
                except BrokenResourceError:
                    # Outbound message queue not open yet
                    pass
                except RuntimeError:
                    self._send_warning(log, "Failed to broadcast light control packet")

            # If the config was updated recently, fire updates in rapid
            # succession to ensure that all the drones get them as soon as
            # possible. If not, but the current light configuration means that
            # we need to control the color from the GCS, wait for at most three
            # seconds before sending the next update to the drones. If the
            # current light configuration means that we are _not_ controlling
            # the lights on the drones, we simply wait until the next
            # configuration change
            if self._rapid_mode:
                await sleep(0.2)
            elif self._active:
                with move_on_after(3):
                    await self._rapid_mode_triggered.wait()
            else:
                await self._rapid_mode_triggered.wait()

            # Fall back to normal mode 5 seconds after the last configuration
            # change
            if self._rapid_mode and monotonic() - self._config_last_updated_at >= 5:
                self._rapid_mode = False
                self._rapid_mode_triggered = Event()

    def _send_warning(self, log, message: str, *args, **kwds) -> None:
        """Prints a warning to the log and suppresses further warnings for the
        next five seconds if needed.
        """
        now = monotonic()
        if now < self._suppress_warnings_until:
            return

        self._suppress_warnings_until = now + 5
        log.warn(message, *args, **kwds)
