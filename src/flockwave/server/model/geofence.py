"""Geofence-related data structures and functions for the server."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional

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
    """Geofence inclusion or exclusion in the form of a polygon."""

    points: list[GeofencePoint] = field(default_factory=list)
    is_inclusion: bool = True

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of a geofence polygon
        in absolute (geodetic) coordinates."""
        return {
            "isInclusion": self.is_inclusion,
            "points": [point.json for point in self.points],
        }


class GeofenceAction(Enum):
    """Actions that a UAV can take when hitting the geofence."""

    REPORT = "report"
    """Report the geofence violation but do nothing."""

    SMART_RETURN = "smartReturn"
    """Attempt to return to the launch site with smart collision avoidance."""

    RETURN = "return"
    """Attempt to return to the launch site without collision avoidance."""

    SMART_LAND = "smartLand"
    """Attempt to land in-place with collision avoidance."""

    LAND = "land"
    """Attempt to land in-place without collision avoidance."""

    STOP = "stop"
    """Stop and hover in-place."""

    SHUT_DOWN = "shutDown"
    """Shut down immediately."""


@dataclass
class GeofenceStatus:
    """Object representing the global status of the geofence on a
    MAVLink-enabled device.
    """

    enabled: bool = False
    """Whether the geofence is enabled globally."""

    actions: list[GeofenceAction] = field(default_factory=list)
    """Actions to take when the geofence is breached, in the order the UAV
    will try them.
    """

    min_altitude: Optional[float] = None
    """Minimum altitude that the drone must maintain, in meters; `None` means no
    minimum altitude requirement.
    """

    max_altitude: Optional[float] = None
    """Maximum altitude that the drone is allowed to fly to; `None` means no
    maximum altitude limit.
    """

    max_distance: Optional[float] = None
    """Maximum distance that the drone is allowed to fly from its home position,
    in meters; `None` means no distance limit.
    """

    polygons: list[GeofencePolygon] = field(default_factory=list)
    """Inclusion and exclusion polygons in the geofence."""

    circles: list[GeofenceCircle] = field(default_factory=list)
    """Inclusion and exclusion circles in the geofence."""

    rally_points: list[GeofencePoint] = field(default_factory=list)
    """Rally points in the geofence."""

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

    - selectively turning on/off certain geofence types
    """

    enabled: Optional[bool] = None
    """Whether the geofence should be enabled; `None` means not to change it"""

    min_altitude: Optional[float] = None
    """Minimum altitude that the drone must maintain; `None` means not to
    change the minimum altitude requirement.
    """

    max_altitude: Optional[float] = None
    """Maximum altitude that the drone is allowed to fly to; `None` means not
    to change the maximum altitude limit.
    """

    max_distance: Optional[float] = None
    """Maximum distance that the drone is allowed to fly from its home
    position; `None` means not to change the distance limit.
    """

    polygons: Optional[list[GeofencePolygon]] = None
    """Inclusion and exclusion polygons in the geofence; `None` means not to
    update the polygons.
    """

    rally_points: Optional[list[GeofencePoint]] = None
    """Rally points in the geofence; `None` means not to update the rally
    points.
    """

    action: Optional[GeofenceAction] = None
    """The action to take if the vehicle hits the geofence; `None` means not to
    update the current geofence action.
    """

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of the geofence configuration in
        absolute (geodetic) coordinates."""
        return {
            "version": 1,
            "enabled": self.enabled,
            "maxAltitude": (
                None
                if self.max_altitude is None
                else round(self.max_altitude, ndigits=3)
            ),
            "minAltitude": (
                None
                if self.min_altitude is None
                else round(self.min_altitude, ndigits=3)
            ),
            "maxDistance": (
                None
                if self.max_altitude is None
                else round(self.max_altitude, ndigits=3)
            ),
            "action": None if self.action is None else self.action.value,
            "polygons": (
                None
                if self.polygons is None
                else [polygon.json for polygon in self.polygons]
            ),
            "rallyPoints": (
                None
                if self.rally_points is None
                else [point.json for point in self.rally_points]
            ),
        }


_geofence_action_descriptions: dict[GeofenceAction, str] = {
    GeofenceAction.REPORT: "report",
    GeofenceAction.SMART_RETURN: "smart return",
    GeofenceAction.RETURN: "return",
    GeofenceAction.SMART_LAND: "smart land",
    GeofenceAction.LAND: "land",
    GeofenceAction.STOP: "stop",
    GeofenceAction.SHUT_DOWN: "shut down",
}


def format_geofence_action(action: GeofenceAction) -> str:
    """Formats the name of the given geofence action."""
    try:
        return (
            _geofence_action_descriptions.get(GeofenceAction(action))
            or "unknown action"
        )
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
