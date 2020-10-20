"""Geofence-related data structures and functions for the server."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional

from flockwave.gps.vectors import GPSCoordinate

__all__ = (
    "GeofenceAction",
    "GeofenceCircle",
    "GeofenceConfigurationRequest",
    "GeofencePoint",
    "GeofencePolygon",
    "GeofenceStatus",
    "format_geofence_action",
    "format_geofence_actions",
)


#: Type specification for points in the geofence
GeofencePoint = GPSCoordinate


@dataclass
class GeofenceCircle:
    """Geofence inclusion or exclusion in the form of a circle around a given
    point.
    """

    center: GeofencePoint
    radius: float
    is_inclusion: bool = True


@dataclass
class GeofencePolygon:
    """Geofence inclusion or exclusion n the form of a polygon."""

    points: List[GeofencePoint] = field(default_factory=list)
    is_inclusion: bool = True


class GeofenceAction(Enum):
    """Actions that a UAV can take when hitting the geofence."""

    #: Report the geofence violation but do nothing
    REPORT = "report"

    #: Attempt to return to the launch site with smart collision avoidance
    SMART_RETURN = "smartReturn"

    #: Attempt to return to the launch site without collision avoidance
    RETURN = "return"

    #: Attempt to land in-place with collision avoidance
    SMART_LAND = "smartLand"

    #: Attempt to land in-place without collision avoidance
    LAND = "land"

    #: Stop and hover in-place
    STOP = "stop"

    #: Shut down immediately
    HALT = "halt"


@dataclass
class GeofenceStatus:
    """Object representing the global status of the geofence on a
    MAVLink-enabled device.
    """

    #: Whether the geofence is enabled globally
    enabled: bool = False

    #: Actions to take when the geofence is breached, in the order the UAV
    #: will try them
    actions: List[GeofenceAction] = field(default_factory=list)

    #: Minimum altitude that the drone must maintain; `None` means no
    #: minimum altitude requirement
    min_altitude: Optional[float] = None

    #: Maximum altitude that the drone is allowed to fly to; `None` means no
    #: maximum altitude limit
    max_altitude: Optional[float] = None

    #: Maximum distance that the drone is allowed to fly from its home
    #: position; `None` means no distance limit
    max_distance: Optional[float] = None

    #: Inclusion and exclusion polygons in the geofence
    polygons: List[GeofencePolygon] = field(default_factory=list)

    #: Inclusion and exclusion circles in the geofence
    circles: List[GeofenceCircle] = field(default_factory=list)

    #: Rally points in the geofence
    rally_points: List[GeofencePoint] = field(default_factory=list)

    def clear_areas(self) -> None:
        """Clears the configured areas (polygons and circles) of the geofence."""
        self.polygons.clear()
        self.circles.clear()

    def clear_rally_points(self) -> None:
        """Clears the configured rally points of the geofence."""
        self.rally_points.clear()

    @property
    def formatted_actions(self) -> str:
        """Returns a human-readable, formatted representation of the geofence
        actions in this object.
        """
        return format_geofence_actions(self.actions or ())


@dataclass
class GeofenceConfigurationRequest:
    """Object representing a geofence configuration object that can be enforced
    on a drone.

    This is admittedly minimal for the time being. We can update it as we
    implement support for more complex geofences. Things that are missing:

    - circular geofences

    - configurable geofence actions

    - selectively turning on/off certain geofence types
    """

    #: Whether the geofence should be enabled; `None` means not to change it
    enabled: Optional[bool] = None

    #: Minimum altitude that the drone must maintain; `None` means not to
    #: change the minimum altitude requirement
    min_altitude: Optional[float] = None

    #: Maximum altitude that the drone is allowed to fly to; `None` means not
    #: to change the maximum altitude limit
    max_altitude: Optional[float] = None

    #: Maximum distance that the drone is allowed to fly from its home
    #: position; `None` means not to change the distance limit
    max_distance: Optional[float] = None

    #: Inclusion and exclusion polygons in the geofence; `None` means not to
    #: update the polygons
    polygons: Optional[List[GeofencePolygon]] = None

    #: Rally points in the geofence; `None` means not to update the rally
    #: points
    rally_points: List[GeofencePoint] = field(default_factory=list)


_geofence_action_descriptions = {
    GeofenceAction.REPORT.value: "report",
    GeofenceAction.SMART_RETURN: "smart return",
    GeofenceAction.RETURN: "return",
    GeofenceAction.SMART_LAND: "smart land",
    GeofenceAction.LAND: "land",
    GeofenceAction.STOP: "stop",
    GeofenceAction.HALT: "shut down",
}


def format_geofence_action(action: GeofenceAction) -> str:
    """Formats the name of the given geofence action."""
    try:
        return _geofence_action_descriptions.get(GeofenceAction(action))
    except Exception:
        return f"unknown action {action!r}"


def format_geofence_actions(actions: Iterable[GeofenceAction]) -> str:
    """Formats the name of multiple geofence actions."""
    names = [format_geofence_action(action) for action in actions]
    if not names:
        return "ignore"
    elif len(names) == 1:
        return names[0]
    elif len(names) == 2:
        return " or ".join(names)
    else:
        return ", ".join(names[:-1]) + " or " + names[-1]
