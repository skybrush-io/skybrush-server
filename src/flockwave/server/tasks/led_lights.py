from abc import ABCMeta, abstractmethod
from blinker import Signal
from enum import Enum
from logging import Logger
from trio import BrokenResourceError, Event, current_time, move_on_after, sleep
from typing import Generic, Optional, Tuple, TypeVar

__all__ = ("LEDLightConfigurationManagerBase",)


#: Type variable representing a packet type that the LED light configuration
#: manager will generate and send
TPacket = TypeVar("TPacket")


#: Type alias for an RGB color
RGBColor = Tuple[int, int, int]


class LightEffectType(Enum):
    """Enumeration holding the type of light effects that could be configured
    on the drones.
    """

    #: GCS is not controlling the LED lights on the drones
    OFF = "off"

    #: GCS is asking the drones to use a solid LED light
    SOLID = "solid"


class LightConfiguration:
    """LED light related configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    color: RGBColor
    effect: LightEffectType

    @classmethod
    def create_solid_color(cls, color: RGBColor) -> "LightConfiguration":
        result = cls()
        result.color = tuple(color)  # type: ignore
        result.effect = LightEffectType.SOLID
        return result

    @classmethod
    def turn_off(cls) -> "LightConfiguration":
        return cls()

    def __init__(self):
        """Constructor."""
        self.color = (0, 0, 0)
        self.effect = LightEffectType.OFF

    def clone(self) -> "LightConfiguration":
        """Makes an exact shallow copy of the configuration object."""
        result = self.__class__()
        result.update_from_json(self.json)
        return result

    @property
    def json(self):
        """Returns the JSON representation of the configuration object."""
        return {"color": list(self.color), "effect": str(self.effect.value)}

    def update_from_json(self, obj):
        """Updates the configuration object from its JSON representation."""
        changed = False

        color = obj.get("color")
        if color:
            if (
                isinstance(color, (list, tuple))
                and len(color) >= 3
                and all(isinstance(x, (int, float)) for x in color)
            ):
                self.color = tuple(int(x) for x in color)  # type: ignore
                # Send a signal even if the color stayed the same; maybe the
                # user sent the same configuration again because some of the
                # drones in the show haven't received the previous request
                changed = True

        effect = obj.get("effect")
        if effect:
            # Send a signal even if the effect stayed the same; maybe the
            # user sent the same configuration again because some of the
            # drones in the show haven't received the previous request
            self.effect = LightEffectType(effect)
            changed = True

        if changed:
            self.updated.send(self)


class LEDLightConfigurationManagerBase(Generic[TPacket], metaclass=ABCMeta):
    """Base class for objects that manage the state of the LED lights on a set
    of drones when the lights are controlled by commands from the GCS.

    The configuration manager may exist in one of two modes: normal or rapid.
    Normal mode is the default. Rapid mode is entered when the light
    configuration changes; during rapid mode, the messages that instruct the
    drones to change their LED colors are fired more frequently than normal to
    ensure that all drones get the changes relatively quickly.

    Attributes:
        message_interval: number of seconds between consecutive messages sent
            from the LED light configuration manager if the manager is not in
            rapid mode
        message_interval_in_rapid_mode: number of seconds between consecutive
            messages sent from the LED light configuration manager if the manager
            is in rapid mode
        rapid_mode_duration: total duration of the rapid mode of the light
            configuration manager. The default value is 5 seconds.
    """

    _active: bool
    _config: Optional[LightConfiguration]
    _config_last_updated_at: float
    _rapid_mode: bool
    _rapid_mode_triggered: Event
    _suppress_warnings_until: float

    message_interval: float
    message_interval_in_rapid_mode: float
    rapid_mode_interval: float

    rapid_mode_duration: float

    def __init__(self):
        """Constructor."""
        self.message_interval = 3
        self.message_interval_in_rapid_mode = 0.2
        self.rapid_mode_duration = 5

        self._active = False
        self._config = None
        self._config_last_updated_at = 0
        self._rapid_mode = False
        self._rapid_mode_triggered = Event()
        self._suppress_warnings_until = 0

    def notify_config_changed(self, config: LightConfiguration) -> None:
        """Notifies the manager that the LED light configuration has changed.

        This function has to be connected to the `show:lights_updated` signal
        of the show extension.
        """
        # Store the configuration
        self._config = config
        self._config_last_updated_at = current_time()

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
            if packet is not None:
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
                await sleep(self.message_interval_in_rapid_mode)
            elif self._active:
                with move_on_after(self.message_interval):
                    await self._rapid_mode_triggered.wait()
            else:
                await self._rapid_mode_triggered.wait()

            # Fall back to normal mode 5 seconds after the last configuration
            # change
            if (
                self._rapid_mode
                and current_time() - self._config_last_updated_at
                >= self.rapid_mode_duration
            ):
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
        now = current_time()
        if now < self._suppress_warnings_until:
            return

        self._suppress_warnings_until = now + 5
        if log:
            log.warn(message, *args, **kwds)
