"""Factory function to create handlers for the "color" command in UAV drivers."""

from colour import Color
from inspect import iscoroutinefunction
from typing import Awaitable, Callable, Optional, Union

from flockwave.server.model.uav import UAV, UAVDriver

__all__ = ("create_color_command_handler",)


def _parse_color(
    red: Optional[Union[str, int]] = None,
    green: Optional[int] = None,
    blue: Optional[int] = None,
) -> Optional[Color]:
    """Parses a color from its red, green and blue components specified as
    integers, or from a string representation, which must be submitted in place
    of the "red" argument (the first positional argument).

    Returns:
        the RGB color as an integer triplet, each component being in the range
        [0; 255], or `None` if the red component is "off", which means to turn
        off any color overrides
    """
    if isinstance(red, str):
        if red.lower() == "off":
            return None
        else:
            # Try to parse the "red" argument as a number
            try:
                red = int(red)
            except ValueError:
                # Parse it as a color name
                return Color(red)

    return Color(
        red=(int(red) or 0) / 255,
        green=(int(green) or 0) / 255,
        blue=(int(blue) or 0) / 255,
    )


async def _color_command_handler(
    driver: UAVDriver,
    uav: UAV,
    red: Optional[Union[str, int]] = None,
    green: Optional[int] = None,
    blue: Optional[int] = None,
) -> str:
    if red is None and green is None and blue is None:
        raise RuntimeError(
            "Please provide the red, green and blue components of the color to set"
        )

    color = _parse_color(red, green, blue)
    if iscoroutinefunction(uav.set_led_color):
        await uav.set_led_color(color)
    else:
        uav.set_led_color(color)

    if color is not None:
        return f"Color set to {color.hex_l}"
    else:
        return "Color override turned off"


def create_color_command_handler() -> Callable[
    [UAVDriver, UAV, Optional[Union[str, int]], Optional[int], Optional[int]],
    Awaitable[str],
]:
    """Creates a generic async command handler function that allows the user to
    set the color of the LED lights on the UAV, assuming that the UAV
    has an async or sync method named `set_led_color()`.

    Assign the function returned from this factory function to the
    `handle_command_color()` method of a UAVDriver_ subclass to make the
    driver support color updates, assuming that the corresponding UAV_ object
    already supports it.
    """
    return _color_command_handler
