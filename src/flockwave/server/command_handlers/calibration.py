"""Factory function to create handlers for the "calib" command in UAV drivers."""

from inspect import isasyncgenfunction, iscoroutinefunction
from typing import Callable, Iterable, Optional

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.commands import ProgressEvents
from flockwave.server.model.uav import UAV, UAVDriver

from .test import STANDARD_COMPONENTS as STANDARD_TEST_COMPONENTS

__all__ = ("create_calibration_command_handler",)


STANDARD_COMPONENTS = dict(STANDARD_TEST_COMPONENTS, level="Level")

SUCCESS_MESSAGES = {"level": "Level calibration executed"}


def create_calibration_command_handler(
    supported_components: Iterable[str],
) -> Callable[[UAVDriver, UAV, Optional[str]], ProgressEvents[str]]:
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
    ) -> ProgressEvents[str]:
        if component is None:
            yield help_text
            return

        if component not in supported:
            raise NotSupportedError

        calibrate_component = uav.calibrate_component
        if calibrate_component is None:
            raise RuntimeError("Component calibration not supported")

        if isasyncgenfunction(calibrate_component):
            async for event in calibrate_component(component):
                yield event
        else:
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

            yield result

    return _calibration_command_handler
