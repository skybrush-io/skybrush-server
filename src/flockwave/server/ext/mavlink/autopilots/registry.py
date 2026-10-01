from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from .unknown import UnknownAutopilot

if TYPE_CHECKING:
    from ..enums import MAVType
    from .base import Autopilot

__all__ = (
    "get_ardupilot_vehicle_factory_by_mavlink_type",
    "get_autopilot_factory_by_mavlink_type",
    "register_for_mavlink_type",
    "register_for_mavlink_vehicle_type",
)


_autopilot_registry: dict[int, type["Autopilot"]] = {}
"""Maps MAV_AUTOPILOT values to the Autopilot subclass handling that autopilot."""


_ardupilot_vehicle_registry: dict[int, type["Autopilot"]] = {}
"""Maps MAV_TYPE values to the ArduPilot subclass handling that vehicle type.

This is a second-level registry: the first level maps MAV_AUTOPILOT to a
family (e.g. ArduPilot), this one narrows an ArduPilot family down to a
specific vehicle type (ArduCopter, ArduPlane, ArduRover).
"""


def get_autopilot_factory_by_mavlink_type(type: int) -> type["Autopilot"]:
    """Returns the Autopilot subclass registered for a given MAV_AUTOPILOT
    value.

    This is the first step in resolving the autopilot class of a UAV: it maps
    the MAV_AUTOPILOT field of the heartbeat message to an autopilot family
    (e.g. ArduPilot or PX4). For the ArduPilot family this is not the final
    answer, since the concrete vehicle class is refined further by
    `ArduPilot.from_vehicle_type_in_heartbeat()` using the MAV_TYPE field.

    Args:
        type: the MAV_AUTOPILOT value from the heartbeat message.

    Returns:
        The registered Autopilot subclass, or `UnknownAutopilot` if no class is
        registered for the given autopilot type.
    """
    return _autopilot_registry.get(type, UnknownAutopilot)


def get_ardupilot_vehicle_factory_by_mavlink_type(
    mav_type: int | "MAVType",
) -> type["Autopilot"] | None:
    """Returns the ArduPilot subclass that handles the given MAV_TYPE, or
    `None` if the vehicle type does not match any known ArduPilot variant.

    Args:
        mav_type: the MAV_TYPE reported in the heartbeat message; either a
            MAVType member or its integer value.
    """
    return _ardupilot_vehicle_registry.get(int(mav_type))


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


V = TypeVar("V", bound="Autopilot")


def register_for_mavlink_vehicle_type(
    *mav_types: "MAVType",
) -> Callable[[type[V]], type[V]]:
    """Class decorator that registers an ArduPilot subclass as the handler for
    one or more MAV_TYPE values.

    This allows the ArduPilot base class to resolve a concrete vehicle class
    from a heartbeat message without importing its own subclasses, which would
    be a circular import.

    Args:
        mav_types: the MAV_TYPE values that the class handles.

    Returns:
        The class decorator.
    """

    def decorator(cls: type[V]) -> type[V]:
        for mav_type in mav_types:
            if mav_type in _ardupilot_vehicle_registry:
                raise RuntimeError(
                    f"MAV_TYPE {mav_type!r} is already registered to "
                    f"{_ardupilot_vehicle_registry[mav_type]!r}"
                )

            _ardupilot_vehicle_registry[mav_type] = cls

        return cls

    return decorator
