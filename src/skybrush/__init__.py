"""Temporary place for functions that are related to the processing of
Skybrush-related file formats, until we find a better place for them.
"""

from .formats import SkybrushBinaryShowFile
from .geofence import get_geofence_configuration_from_show_specification
from .lights import get_light_program_from_show_specification
from .player import TrajectoryPlayer
from .show import (
    get_altitude_reference_from_show_specification,
    get_coordinate_system_from_show_specification,
    get_group_index_from_show_specification,
    get_home_position_from_show_specification,
    get_rth_plan_from_show_specification,
    get_trajectory_from_show_specification,
)
from .trajectory import TrajectorySpecification

__all__ = (
    "get_altitude_reference_from_show_specification",
    "get_coordinate_system_from_show_specification",
    "get_geofence_configuration_from_show_specification",
    "get_group_index_from_show_specification",
    "get_home_position_from_show_specification",
    "get_light_program_from_show_specification",
    "get_rth_plan_from_show_specification",
    "get_trajectory_from_show_specification",
    "SkybrushBinaryShowFile",
    "TrajectoryPlayer",
    "TrajectorySpecification",
)
