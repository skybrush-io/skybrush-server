from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .unknown import UnknownAutopilot

if TYPE_CHECKING:
    from .base import Autopilot

__all__ = ("get_autopilot_factory_by_mavlink_type", "register_for_mavlink_type")


_autopilot_registry: dict[int, type["Autopilot"]] = {}


def get_autopilot_factory_by_mavlink_type(type: int) -> type["Autopilot"]:
    return _autopilot_registry.get(type, UnknownAutopilot)


def register_for_mavlink_type(
    mavlink_type: int,
) -> Callable[[type["Autopilot"]], type["Autopilot"]]:
    """Class decorator to register an Autopilot subclass for a given MAVLink
    autopilot type.

    Args:
        mavlink_type: The MAVLink autopilot type to register the class for.

    Returns:
        The class decorator.
    """

    def decorator(cls: type["Autopilot"]) -> type["Autopilot"]:
        if cls in _autopilot_registry:
            raise RuntimeError(f"{cls!r} is already registered")

        _autopilot_registry[mavlink_type] = cls
        return cls

    return decorator
