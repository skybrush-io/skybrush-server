from abc import ABCMeta, abstractmethod
from logging import Logger
from time import monotonic
from trio import BrokenResourceError, Event, move_on_after, sleep
from typing import Generic, Optional, TypeVar

from flockwave.server.ext.show.config import LightConfiguration, LightEffectType

__all__ = ("LEDLightConfigurationManagerBase",)


#: Type variable representing a packet type that the LED light configuration
#: manager will generate and send
TPacket = TypeVar("TPacket")


class LEDLightConfigurationManagerBase(Generic[TPacket], metaclass=ABCMeta):
    """Base class for objects that manage the state of the LED lights on a set
    of drones when the lights are controlled by commands from the GCS.
    """

    def __init__(self):
        """Constructor."""
        self._active: bool = False
        self._config: Optional[LightConfiguration] = None
        self._config_last_updated_at: float = 0
        self._rapid_mode: bool = False
        self._rapid_mode_triggered: Event = Event()
        self._suppress_warnings_until: float = 0

    def notify_config_changed(self, config: LightConfiguration) -> None:
        """Notifies the manager that the LED light configuration has changed.

        This function has to be connected to the `show:lights_updated` signal
        of the show extension.
        """
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
        log = self._get_logger()
        while True:
            try:
                await self._run(log)
            except Exception:
                if log:
                    log.exception(
                        "LED light control task stopped unexpectedly, restarting..."
                    )
                await sleep(0.5)

    async def _run(self, log: Optional[Logger]) -> None:
        while True:
            # Note that we might need to send a packet even if we are inactive
            # to ensure that the drones are informed when the GCS stops sending
            # further LED control commands and switches to "off" mode
            packet = (
                self._create_light_control_packet(self._config)
                if self._config is not None
                else None
            )
            if packet:
                try:
                    await self._send_light_control_packet(packet)
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

    @abstractmethod
    def _create_light_control_packet(self, config: LightConfiguration) -> TPacket:
        """Creates a light control packet that must be sent to the group of
        drones managed by this extension, assuming the given light
        configuration on the GCS.

        Returns:
            the packet to send to the drones, or `None` if no packet has to be
            sent
        """
        raise NotImplementedError

    def _get_logger(self) -> Optional[Logger]:
        """Returns the logger that the manager can use for logging warning
        messages, or `None` if the manager should not use a logger.

        The default implementation returns `None`, unconditionally.
        """
        return None

    @abstractmethod
    async def _send_light_control_packet(self, packet: TPacket) -> None:
        """Sends a light control packet to the group of drones that this manager
        object is managing.
        """
        raise NotImplementedError

    def _send_warning(self, log: Optional[Logger], message: str, *args, **kwds) -> None:
        """Prints a warning to the log and suppresses further warnings for the
        next five seconds if needed.
        """
        now = monotonic()
        if now < self._suppress_warnings_until:
            return

        self._suppress_warnings_until = now + 5
        if log:
            log.warn(message, *args, **kwds)
