"""Functions related to generic onboard parameter handling on UAVs."""

from typing import Awaitable, Callable, Optional, Union

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.uav import UAV, UAVDriver
from flockwave.server.utils import format_number_nicely

__all__ = ("create_parameter_command_handler",)


def create_parameter_command_handler(
    name_validator: Optional[Callable[[str], str]] = None
) -> Callable[..., Awaitable[str]]:
    """Creates a generic async command handler function that allows the user to
    retrieve or set the value of a parameter of a UAV, assuming that the UAV
    has async methods named `get_parameter()` and `set_parameter()`.

    Assign the function returned from this factory function to the
    `handle_command_param()` method of a UAVDriver_ subclass to make the
    driver support parameter retrievals and updates, assuming that the
    corresponding UAV_ object already supports it.

    The handler supports the following command syntaxes:

    - `param name` retrieves the current value of the parameter with the given
    name (`name`)

    - `param name value` or `param name=value` sets the parameter with the given
    name to a new value

    Parameter names must be strings. Parameter values may be specified either as
    strings or as floats. Strings that can be cast into numbers will be cast
    into numbers. Strings that _cannot_ be cast into numbers will throw an
    error.

    Parameters:
        name_validator: optional function that will take the parameter name
            entered by the user and must return a validated version; this can
            be used, e.g., to convert all parameter names to uppercase if the
            UAV expects that

    Returns:
        the name of the parameter and its current (or new) value
    """

    async def handler(
        driver: UAVDriver,
        uav: UAV,
        name: Optional[str] = None,
        value: Optional[Union[str, float]] = None,
    ) -> str:
        if not name:
            raise RuntimeError("Missing parameter name")

        name = str(name)
        if "=" in name and value is None:
            name, value = name.split("=", 1)

        if name_validator:
            try:
                name = name_validator(name)
            except Exception:
                raise RuntimeError(f"Invalid parameter name: {name}")

        if value is not None:
            try:
                value = float(value)
            except ValueError:
                raise RuntimeError(f"Invalid parameter value: {value}")
            if value.is_integer():
                value = int(value)
            if not hasattr(uav, "set_parameter"):
                raise NotSupportedError(
                    "Setting parameters is not supported on this UAV"
                )
            try:
                await uav.set_parameter(name, value)  # type: ignore
            except KeyError:
                raise RuntimeError(f"No such parameter: {name}")

        if not hasattr(uav, "get_parameter"):
            raise NotSupportedError(
                "Retrieving parameters is not supported on this UAV"
            )

        try:
            value = await uav.get_parameter(name, fetch=True)  # type: ignore
        except KeyError:
            raise RuntimeError(f"No such parameter: {name}")

        if isinstance(value, (int, float)):
            value = format_number_nicely(value)

        return f"{name} = {value}"

    return handler
