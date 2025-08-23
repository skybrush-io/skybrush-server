from __future__ import annotations

from typing import Callable, TYPE_CHECKING, Type

from .unknown import UnknownAutopilot

if TYPE_CHECKING:
    from .base import Autopilot

__all__ = ("get_autopilot_factory_by_mavlink_type", "register_for_mavlink_type")


_autopilot_registry: dict[int, Type["Autopilot"]] = {}


def get_autopilot_factory_by_mavlink_type(type: int) -> Type["Autopilot"]:
    return _autopilot_registry.get(type, UnknownAutopilot)


def register_for_mavlink_type(
    type: int,
) -> Callable[[Type["Autopilot"]], Type["Autopilot"]]:
    """Class decorator to register an Autopilot subclass for a given MAVLink
    autopilot type.

    Args:
        type: The MAVLink autopilot type to register the class for.

    Returns:
        The class decorator.
    """

    def decorator(cls: Type["Autopilot"]) -> Type["Autopilot"]:
        if cls in _autopilot_registry:
            raise RuntimeError(f"{cls!r} is already registered")

        _autopilot_registry[type] = cls
        return cls

    return decorator
