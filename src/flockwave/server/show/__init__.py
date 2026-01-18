"""Temporary place for functions that are related to the processing of
Skybrush-related file formats, until we find a better place for them.
"""

from .flight_area import get_flight_area_configuration_from_show_specification
from .formats import SkybrushBinaryShowFile
from .geofence import get_geofence_configuration_from_show_specification
from .lights import get_light_program_from_show_specification
from .player import LightPlayer, TrajectoryPlayer
from .safety import get_safety_configuration_from_show_specification
from .specification import (
    ShowSpecification,
    get_altitude_reference_from_show_specification,
    get_coordinate_system_from_show_specification,
    get_drone_count_from_show_specification,
    get_group_index_from_show_specification,
    get_home_position_from_show_specification,
    get_trajectory_from_show_specification,
    is_coordinate_system_in_show_specification_geodetic,
)
from .trajectory import TrajectorySpecification

__all__ = (
    "get_altitude_reference_from_show_specification",
    "get_coordinate_system_from_show_specification",
    "get_drone_count_from_show_specification",
    "get_flight_area_configuration_from_show_specification",
    "get_geofence_configuration_from_show_specification",
    "get_group_index_from_show_specification",
    "get_home_position_from_show_specification",
    "get_light_program_from_show_specification",
    "get_safety_configuration_from_show_specification",
    "get_trajectory_from_show_specification",
    "is_coordinate_system_in_show_specification_geodetic",
    "LightPlayer",
    "ShowSpecification",
    "SkybrushBinaryShowFile",
    "TrajectoryPlayer",
    "TrajectorySpecification",
)
