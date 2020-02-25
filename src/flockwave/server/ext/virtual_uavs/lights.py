"""Interface specification and implementation of the light controller object
on the UAVs.
"""

from abc import abstractmethod
from typing import Callable, Iterable, List, Optional, Tuple, Union

from flockwave.spec.errors import FlockwaveErrorCode

__all__ = (
    "color_to_rgb565",
    "LightController",
    "ModularLightController",
    "DefaultLightController",
)


#: Type specification for an RGB color triplet
RGBColor = Tuple[int, int, int]

#: Type specification of a light module for a modular light controller
LightModule = Callable[[float, RGBColor], RGBColor]


#: Object listing a few well-known colors
class Colors:
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    RED = (255, 0, 0)
    ORANGE = (255, 128, 0)


def color_to_rgb565(color: RGBColor) -> int:
    """Converts a color given as an RGB triplet into its RGB565
    representation.

    Parameters:
        color: the color to convert

    Returns:
        int: the color in its RGB565 representation
    """
    red, green, blue = color
    return (
        (((red >> 3) & 0x1F) << 11)
        + (((green >> 2) & 0x3F) << 5)
        + ((blue >> 3) & 0x1F)
    )


class LightController:
    """Light controller object that can be passed a timestamp and a base
    color and that will return the color that the virtual LED light of the
    UAV should show at the given timestamp.
    """

    @abstractmethod
    def evaluate(self, timestamp: float, base_color: RGBColor = Colors.BLACK):
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

    def evaluate(self, timestamp: float, base_color: RGBColor = Colors.BLACK):
        result = base_color
        for module in self._modules:
            result = module(timestamp, result)
        return result


def constant_color(color: RGBColor) -> LightModule:
    """Light module factory that returns a light module that always returns the
    same color.
    """

    def module(timestamp: float, base_color: RGBColor):
        return color

    return module


class DefaultLightController(ModularLightController):
    """Modular light controller with a few predefined modules that make sense
    for a virtual UAV.
    """

    def __init__(self, owner=None):
        super().__init__(self._create_default_modules())
        self.owner = owner

    def _create_default_modules(self) -> List[LightModuleLike]:
        """Returns the default set of modules to use in this controller."""
        result = [constant_color(Colors.WHITE), self._error_module]
        return result

    def _error_module(self, timestamp: float, color: RGBColor) -> RGBColor:
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
