"""Types specific to the mission planning and management extension."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

__all__ = ("MissionPlanner",)


@dataclass
class MissionPlan:
    """Simple dataclass to model the object that a mission planner should
    return.
    """

    format: str
    """Format of the returned mission. This may indicate a Skybrush show
    file, an ArduPilot-style mission or anything else. It is the responsibility
    of the client to handle the payload depending on the format supplied here.
    """

    payload: Any
    """The actual mission description. For Skybrush shows, this can be the
    JSON representation of the show. For ArduPilot-style missions, this can be
    a string in the standard textual mission format supported by Mission Planner
    or QGroundControl.
    """


MissionPlanner = Union[
    Callable[..., MissionPlan], Callable[..., Awaitable[MissionPlan]]
]
"""Type alias for mission planners that can be called with an arbitrary
number of keyword arguments and that return a mission description.
"""
