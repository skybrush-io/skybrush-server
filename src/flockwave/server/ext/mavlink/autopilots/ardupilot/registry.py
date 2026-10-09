"""Registry that maps MAV_TYPE values to ArduPilot vehicle variants."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from flockwave.server.ext.mavlink.enums import MAVAutopilot, MAVType

from .base import ArduPilot

if TYPE_CHECKING:
    from flockwave.server.ext.mavlink.types import MAVLinkMessage

__all__ = (
    "get_ardupilot_factory_for_heartbeat",
    "get_ardupilot_vehicle_factory_by_mavlink_type",
    "register_for_mavlink_vehicle_type",
)


log = logging.getLogger(__name__)


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


def get_ardupilot_factory_for_heartbeat(
    message: MAVLinkMessage,
) -> type[ArduPilot]:
    """Returns the ArduPilot variant that corresponds to the MAV_TYPE field of
    the given heartbeat message.

    Args:
        message: a heartbeat message sent by an ArduPilot autopilot, i.e. one
            whose MAV_AUTOPILOT field is `MAVAutopilot.ARDUPILOTMEGA`.

    Returns:
        The ArduPilot subclass registered for the MAV_TYPE value of the
        message, or `ArduPilot` itself when the MAV_TYPE is unknown or not
        handled by any of the ArduPilot variants.

    Raises:
        ValueError: if the message was not sent by an ArduPilot autopilot.
    """
    if message.autopilot != MAVAutopilot.ARDUPILOTMEGA:
        raise ValueError(
            f"Cannot construct ArduPilot factory from autopilot class {message.autopilot}"
        )

    try:
        vehicle_type = MAVType(message.type)
    except ValueError:
        log.warning(
            f"Unknown heartbeat MAV_TYPE: {message.type}; cannot determine "
            "ArduPilot variant; falling back to generic ArduPilot"
        )
        return ArduPilot

    result = get_ardupilot_vehicle_factory_by_mavlink_type(vehicle_type)
    return result if result is not None else ArduPilot


def get_ardupilot_vehicle_factory_by_mavlink_type(
    mav_type: int | MAVType,
) -> type[ArduPilot] | None:
    """Returns the ArduPilot subclass that handles the given MAV_TYPE, or
    `None` if the vehicle type does not match any known ArduPilot variant.

    Args:
        mav_type: the MAV_TYPE reported in the heartbeat message; either a
            MAVType member or its integer value.
    """
    return _ardupilot_vehicle_registry.get(int(mav_type))
