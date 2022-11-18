"""Mission-related data structures and functions for the server."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


__all__ = (
    "Altitude",
    "AltitudeReference",
    "HeadingMode",
    "MissionItem",
    "MissionItemBundle",
    "MissionItemType",
    "PayloadAction",
)


@dataclass
class Altitude:
    """Representation of an altitude relative to a reference altitude in [m]."""

    value: float
    """The altitude value in [m]."""

    reference: AltitudeReference
    """The altitude reference."""


class AltitudeReference(Enum):
    """Altitude references supported by Skybrush."""

    HOME = "home"
    """Altitude reference is the home altitude."""

    MSL = "msl"
    """Altitude reference is mean sea level."""

    # TODO: add ground / terrain


@dataclass
class Heading:
    """Representation of a heading change action."""

    mode: HeadingMode
    """The heading mode to use."""

    value: Optional[float] = None
    """Optinal fixed heading in [deg]."""

    rate: Optional[float] = 30
    """Optional heading change rate in [deg/s]."""


class HeadingMode(Enum):
    """Heading modes supported by Skybrush."""

    ABSOLUTE = "absolute"
    """Heading is given as a fixed absolute value."""

    WAYPOINT = "waypoint"
    """Heading is set to track next waypoint."""


class MissionItem(TypedDict):
    """Representation of a mission item in a format that comes directly from
    Skybrush Live.
    """

    type: str
    """The type of the mission item."""

    parameters: Optional[Dict[str, Any]]
    """The parameters of the mission item; exact parameters are dependent on
    the type of the item.
    """


class MissionItemBundle(TypedDict):
    """Representation of a collection of mission items submitted from Skybrush
    Live.
    """

    version: int
    """The version number of the bundle; currently it is always 1."""

    name: Optional[str]
    """The name of the mission to upload to the drone."""

    items: List[MissionItem]
    """The list of mission items in the bundle."""


class MissionItemType(Enum):
    """Mission item types supported by Skybrush."""

    CHANGE_ALTITUDE = "changeAltitude"
    """Command to change the altitude."""

    CHANGE_HEADING = "changeHeading"
    """Command to change the heading (yaw) of the UAV."""

    GO_TO = "goTo"
    """Command to go to a desired position in 2D or 3D space."""

    LAND = "land"
    """Command to land the UAV."""

    RETURN_TO_HOME = "returnToHome"
    """Command to return to home."""

    SET_PAYLOAD = "setPayload"
    """Command to set a given payload to a desired state."""

    TAKEOFF = "takeoff"
    """Command to takeoff."""


class PayloadAction(Enum):
    """Payload action types."""

    TURN_OFF = "off"
    """Turn off the payload."""

    TURN_ON = "on"
    """Turn on the payload."""

    # TODO: add support for these below later on

    # TRIGGER = "trigger"
    # """Trigger the payload once."""

    # TRIGGER_AT_INTERVAL = "interval"
    # """Trigger the payload repeatedly at given time intervals."""

    # TRIGGER_AT_DISTANCE = "distance"
    # """Trigger the payload repeatedly at given distance travelled."""
