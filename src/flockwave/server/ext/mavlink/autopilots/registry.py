from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from .unknown import UnknownAutopilot

if TYPE_CHECKING:
    from .base import Autopilot

__all__ = (
    "get_autopilot_factory_by_mavlink_type",
    "register_for_mavlink_type",
)


_autopilot_registry: dict[int, type["Autopilot"]] = {}
"""Maps MAV_AUTOPILOT values to the Autopilot subclass handling that autopilot.

ArduPilot refines this mapping further, down to the concrete vehicle variant,
in its own registry module.
"""


def get_autopilot_factory_by_mavlink_type(type: int) -> type["Autopilot"]:
    """Returns the Autopilot subclass registered for a given MAV_AUTOPILOT
    value.

    This is the first step in resolving the autopilot class of a UAV: it maps
    the MAV_AUTOPILOT field of the heartbeat message to an autopilot family
    (e.g. ArduPilot or PX4). For the ArduPilot family this is not the final
    answer, since the concrete vehicle class is refined further from the
    MAV_TYPE field by `get_ardupilot_factory_for_heartbeat()`.

    Args:
        type: the MAV_AUTOPILOT value from the heartbeat message.

    Returns:
        The registered Autopilot subclass, or `UnknownAutopilot` if no class is
        registered for the given autopilot type.
    """
    return _autopilot_registry.get(type, UnknownAutopilot)


T = TypeVar("T", bound="Autopilot")


def register_for_mavlink_type(
    mavlink_type: int,
) -> Callable[[type[T]], type[T]]:
    """Class decorator to register an Autopilot subclass for a given MAVLink
    autopilot type.

    Args:
        mavlink_type: The MAVLink autopilot type to register the class for.

    Returns:
        The class decorator.
    """

    def decorator(cls: type[T]) -> type[T]:
        if cls in _autopilot_registry:
            raise RuntimeError(f"{cls!r} is already registered")

        _autopilot_registry[mavlink_type] = cls
        return cls

    return decorator
