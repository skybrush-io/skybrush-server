"""Factory function to create handlers for the "version" command in UAV drivers."""

from inspect import iscoroutinefunction
from typing import Callable

from flockwave.server.model.uav import UAV, UAVDriver

__all__ = ("create_version_command_handler",)


async def _version_command_handler(driver: UAVDriver, uav: UAV) -> str:
    if iscoroutinefunction(uav.get_version_info):
        version_info = await uav.get_version_info()
    else:
        version_info = uav.get_version_info()

    if version_info:
        parts = [f"{key} = {version_info[key]}" for key in sorted(version_info.keys())]
        return "\n".join(parts)
    else:
        return "No version information available"


def create_version_command_handler() -> Callable[[UAVDriver, UAV], str]:
    """Creates a generic async command handler function that allows the user to
    retrieve the version information of the UAV, assuming that the UAV
    has an async method named `get_version_info()`.

    Assign the function returned from this factory function to the
    `handle_command_version()` method of a UAVDriver_ subclass to make the
    driver version number retrievals, assuming that the corresponding UAV_
    object already supports it.
    """
    return _version_command_handler
