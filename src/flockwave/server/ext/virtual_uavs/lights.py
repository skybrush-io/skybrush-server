"""Interface specification and implementation of the light controller object
on the UAVs.
"""

from abc import abstractmethod
from colour import Color
from time import monotonic
from typing import Callable, Iterable, List, Optional, Union

from pyledctrl.player import Player

from flockwave.spec.errors import FlockwaveErrorCode

__all__ = ("LightController", "ModularLightController", "DefaultLightController")


#: Type specification of a light module for a modular light controller
LightModule = Callable[[float, Color], Color]


#: Object listing a few well-known colors
class Colors:
    BLACK = Color("black")
    WHITE = Color("white")
    RED = Color(rgb=(1, 0, 0))
    ORANGE = Color(rgb=(1, 0.5, 0))


class LightController:
    """Light controller object that can be passed a timestamp and a base
    color and that will return the color that the virtual LED light of the
    UAV should show at the given timestamp.
    """

    @abstractmethod
    def evaluate(self, timestamp: float, base_color: Color = Colors.BLACK):
        """Calculates the RGB triplet of the light that should be shown on the
        UAV at the given timestamp.

        Parameters:
            timestamp: the timestamp when the light should be evaluated,
                expressed as the number of seconds since the UNIX epoch
            base_color: the base color to return if the controller does not
                want to change the color shown by the UAV
        """
        raise NotImplementedError


#: Type specification for objects that can be converted into a light module
LightModuleLike = Union[LightController, LightModule]


class ModularLightController(LightController):
    """Base implementation of a modular light controller object that consists
    of a chain of light modules that are evaluated one after the other.
    Each light module receives the output of the previous module as the base
    color and may decide to pass through the color intact, override it
    completely or mix another color into it.
    """

    def __init__(self, modules: Optional[Iterable[LightModuleLike]] = None):
        """Constructor."""
        super().__init__()
        self._modules = []  # type: List[LightModule]

        for module in modules or []:
            self.add_module(module)

    def add_module(self, module: Union[LightController, LightModule]) -> None:
        """Adds a new module to the light controller."""
        if isinstance(module, LightController):
            module = module.evaluate
        self._modules.append(module)

    def evaluate(self, timestamp: float, base_color: Color = Colors.BLACK):
        result = base_color
        for module in self._modules:
            result = module(timestamp, result)
        return result


def constant_color(color: Color) -> LightModule:
    """Light module factory that returns a light module that always returns the
    same color.
    """

    def module(timestamp: float, base_color: Color):
        return color

    return module


class DefaultLightController(ModularLightController):
    """Modular light controller with a few predefined modules that make sense
    for a virtual UAV.
    """

    _light_program_player: Optional[Player]
    _light_program_start_time: Optional[float]

    _where_are_you_duration_ms: float
    _where_are_you_start_time: Optional[float]

    _override: Optional[Color]

    def __init__(self, owner=None):
        super().__init__(self._create_default_modules())

        self.owner = owner

        self._light_program_player = None
        self._light_program_start_time = None

        self._where_are_you_duration_ms = 1000
        self._where_are_you_start_time = None

        self._override = None

    def clear_light_program(self) -> None:
        """Clears the currently loaded light program."""
        self._light_program_player = None
        self._light_program_start_time = None

    def load_light_program(self, light_program: bytes) -> None:
        """Loads a light program that will be played when `play_light_program()`
        is called.
        """
        self._light_program_player = Player.from_bytes(light_program)

    @property
    def override(self) -> Optional[Color]:
        return self._override

    @override.setter
    def override(self, value: Optional[Color]):
        if value is not None and not isinstance(value, Color):
            raise TypeError(f"Color or None expected, got {type(value)!r}")
        self._override = value

    def play_light_program(self) -> None:
        """Starts playing the current light program.

        This function is a no-op if there is no light program loaded.
        """
        if self._light_program_player is not None:
            self._light_program_start_time = monotonic()

    def stop_light_program(self) -> None:
        """Stops playing the current light program.

        This function is a no-op if there is no light program being played.
        """
        self._light_program_start_time = None

    def _create_default_modules(self) -> List[LightModuleLike]:
        """Returns the default set of modules to use in this controller."""
        result = [
            constant_color(Colors.WHITE),
            self._light_program_module,
            self._error_module,
            self._override_module,
            self._where_are_you_module,
        ]
        return result

    def where_are_you(self, duration: float = 1) -> None:
        """Initiates a 'where are you' command in the light program.

        Parameters:
            duration: duration of the light signal in seconds
        """
        self._where_are_you_start_time = monotonic()
        self._where_are_you_duration_ms = duration * 1000

    def _error_module(self, timestamp: float, color: Color) -> Color:
        """Lighting module that sets the color unconditionally to red in case
        of an error, orange in case of a warning, or flashing orange in RTH
        mode.
        """
        errors = self.owner.errors
        if errors:
            max_code = max(errors)
            if max_code >= 128:
                return Colors.RED
            elif max_code >= 64:
                return Colors.ORANGE
            elif max_code == FlockwaveErrorCode.RETURN_TO_HOME:
                return (
                    Colors.ORANGE
                    if (timestamp - int(timestamp)) >= 0.5
                    else Colors.BLACK
                )

        return color

    def _light_program_module(self, timestamp: float, color: Color) -> Color:
        """Lighting module that plays back a predefined light program in
        `pyledctrl` compiled format.
        """
        if self._light_program_player and self._light_program_start_time:
            if self._light_program_player.ended:
                self.stop_light_program()
            else:
                dt = timestamp - self._light_program_start_time
                r, g, b = self._light_program_player.get_color_at(dt)
                color = Color(rgb=(r / 255, g / 255, b / 255))

        return color

    def _override_module(self, timestamp: float, color: Color) -> Color:
        """Lighting module that overrides the input color unconditionally with
        a color specified by the user with the `override` property of the
        default light controller.
        """
        return self._override or color

    def _where_are_you_module(self, timestamp: float, color: Color) -> Color:
        """Lighting module that sets the color to flashing white for a while
        to be able to find it in on the field or on the map.
        """
        if self._where_are_you_start_time is not None:
            dt = int((timestamp - self._where_are_you_start_time) * 1000)
            if dt < self._where_are_you_duration_ms:
                return Colors.WHITE if ((dt // 200) % 2) == 0 else Colors.BLACK
            else:
                self._where_are_you_start_time = None

        return color
