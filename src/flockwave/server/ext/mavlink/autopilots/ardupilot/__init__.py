"""Implementations of ArduPilot-specific functionality."""

from .arducopter import (
    ArduCopter,
    ArduCopterWithSkybrush,
)
from .arduplane import ArduPlane
from .ardurover import ArduRover
from .base import (
    ArduPilot,
    decode_parameters_from_packed_format,
    encode_parameters_to_packed_format,
)
from .registry import get_ardupilot_vehicle_factory_by_mavlink_type

__all__ = (
    "ArduCopter",
    "ArduCopterWithSkybrush",
    "ArduPilot",
    "ArduPlane",
    "ArduRover",
    "decode_parameters_from_packed_format",
    "encode_parameters_to_packed_format",
    "get_ardupilot_vehicle_factory_by_mavlink_type",
)
