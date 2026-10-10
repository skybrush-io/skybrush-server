"""Registry that maps MAV_TYPE values to ArduPilot vehicle variants."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from flockwave.server.ext.mavlink.enums import MAVType

from .base import ArduPilot

__all__ = (
    "get_ardupilot_vehicle_factory_by_mavlink_type",
    "register_for_mavlink_vehicle_type",
)


_ardupilot_vehicle_registry: dict[int, type[ArduPilot]] = {}
"""Maps MAV_TYPE values to the ArduPilot subclass handling that vehicle type.

This is a second-level registry: the first level maps MAV_AUTOPILOT to a
family (e.g. ArduPilot), this one narrows an ArduPilot family down to a
specific vehicle type (ArduCopter, ArduPlane, ArduRover).
"""


V = TypeVar("V", bound="ArduPilot")


def register_for_mavlink_vehicle_type(
    *mav_types: MAVType,
) -> Callable[[type[V]], type[V]]:
    """Class decorator that registers an ArduPilot subclass as the handler for
    one or more MAV_TYPE values.

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


def get_ardupilot_vehicle_factory_by_mavlink_type(
    mav_type: int | MAVType,
) -> type[ArduPilot]:
    """Returns the ArduPilot subclass that handles the given MAV_TYPE value.

    Args:
        mav_type: the MAV_TYPE reported in the heartbeat message; either a
            MAVType member or its integer value.

    Returns:
        The registered Ardupilot subclass, or `ArduPilot` base class if no class is
        registered for the given autopilot type.
    """
    return _ardupilot_vehicle_registry.get(int(mav_type), ArduPilot)
