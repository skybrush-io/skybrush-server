"""Temporary place for functions that are related to the processing of
Skybrush-related file formats, until we find a better place for them.
"""

from .lights import get_skybrush_light_program_from_show_specification
from .trajectory import (
    get_skybrush_trajectory_from_show_specification,
    TrajectorySpecification,
)

__all__ = (
    "get_skybrush_light_program_from_show_specification",
    "get_skybrush_trajectory_from_show_specification",
    "TrajectorySpecification",
)
