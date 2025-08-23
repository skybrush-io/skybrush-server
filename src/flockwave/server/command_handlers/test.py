"""Factory function to create handlers for the "test" command in UAV drivers."""

from inspect import isasyncgenfunction, iscoroutinefunction
from typing import Callable, Iterable, Optional

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.commands import ProgressEvents
from flockwave.server.model.uav import UAV, UAVDriver

__all__ = ("create_test_command_handler",)


STANDARD_COMPONENTS = {
    "accel": "Accelerometer",
    "baro": "Pressure sensor",
    "battery": "Battery",
    "compass": "Compass",
    "gyro": "Gyroscope",
    "led": "LED",
    "motor": "Motor",
}


def create_test_command_handler(
    supported_components: Iterable[str],
) -> Callable[[UAVDriver, UAV, Optional[str]], ProgressEvents[str]]:
    """Creates a generic async command handler function that allows the user to
    test certain components of the UAV, assuming that the UAV has an async or
    sync method named `test_component()` that accepts a single component name
    as a string. Async functions returning an iterator that yields Progress_
    objects is also accepted.

    Assign the function returned from this factory function to the
    `handle_command_test()` method of a UAVDriver_ subclass to make the
    driver support component tests, assuming that the corresponding UAV_ object
    already supports it.
    """
    supported = set(supported_components)

    options = "|".join(sorted(supported))
    help_text = f"Usage: test <{options}>"

    async def _test_command_handler(
        driver: UAVDriver,
        uav: UAV,
        component: Optional[str] = None,
    ) -> ProgressEvents[str]:
        if component is None:
            yield help_text
            return

        if component not in supported:
            raise NotSupportedError

        test_component = uav.test_component
        if test_component is None:
            raise RuntimeError("Component tests not supported")

        if isasyncgenfunction(test_component):
            async for event in test_component(component):
                yield event
        else:
            if iscoroutinefunction(test_component):
                result = await test_component(component)
            else:
                result = test_component(component)

            if not isinstance(result, str):
                component_name = f"Component {component!r}"
                result = (
                    str(STANDARD_COMPONENTS.get(component or "", component_name))
                    + " test executed"
                )

            yield result

    return _test_command_handler
