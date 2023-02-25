"""Factory function to create handlers for the "calib" command in UAV drivers."""

from inspect import iscoroutinefunction
from typing import Awaitable, Callable, Iterable, Optional

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.uav import UAV, UAVDriver

__all__ = ("create_calibration_command_handler",)


STANDARD_COMPONENTS = {
    "baro": "Pressure sensor",
    "compass": "Compass",
    "gyro": "Gyroscope",
    "level": "Level",
}

SUCCESS_MESSAGES = {"level": "Level calibration executed"}


def create_calibration_command_handler(
    supported_components: Iterable[str],
) -> Callable[[UAVDriver, UAV, Optional[str]], Awaitable[str]]:
    """Creates a generic async command handler function that allows the user to
    calibrate certain components of the UAV, assuming that the UAV has an async or
    sync method named `calibrate_component()` that accepts a single component name
    as a string.

    Assign the function returned from this factory function to the
    `handle_command_calib()` method of a UAVDriver_ subclass to make the
    driver support component tests, assuming that the corresponding UAV_ object
    already supports it.
    """
    supported = set(supported_components)

    options = "|".join(sorted(supported))
    help_text = f"Usage: calib <{options}>"

    async def _calibration_command_handler(
        driver: UAVDriver,
        uav: UAV,
        component: Optional[str] = None,
    ) -> str:
        if component is None:
            return help_text

        if component not in supported:
            raise NotSupportedError

        calibrate_component = getattr(uav, "calibrate_component")
        if calibrate_component is None:
            raise RuntimeError("Component calibration not supported")

        if iscoroutinefunction(calibrate_component):
            result = await calibrate_component(component)
        else:
            result = calibrate_component(component)

        if not isinstance(result, str):
            component_name = f"Component {component!r}"
            result = SUCCESS_MESSAGES.get(component or "") or (
                str(STANDARD_COMPONENTS.get(component or "", component_name))
                + " calibrated"
            )

        return result

    return _calibration_command_handler
