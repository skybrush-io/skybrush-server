"""Geofence-related data structures and functions for the server."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional

from flockwave.gps.vectors import GPSCoordinate

__all__ = (
    "GeofenceAction",
    "GeofenceCircle",
    "GeofencePoint",
    "GeofencePolygon",
    "GeofenceStatus",
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


class GeofenceAction(IntEnum):
    """Actions that a UAV can take when hitting the geofence."""

    REPORT_ONLY = 0
    RTH_OR_LAND = 1
    ALWAYS_LAND = 2
    SMART_RTH_RTH_OR_LAND = 3
    BRAKE_OR_LAND = 4

    @staticmethod
    def format(action):
        """Formats the geofence action as a human-readable string."""
        return format_fence_action(action)


@dataclass
class GeofenceStatus:
    """Object representing the global status of the geofence on a
    MAVLink-enabled device.
    """

    #: Whether the geofence is enabled globally
    enabled: bool = False

    #: Action to take when the geofence is breached
    action: GeofenceAction = GeofenceAction.REPORT_ONLY

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


_geofence_action_names = {
    0: "report only",
    1: "RTH or land",
    2: "always land",
    3: "smart RTH, RTH or land",
    4: "brake or land",
}


def format_fence_action(code: int) -> str:
    """Formats the name of the given geofence action."""
    try:
        return _geofence_action_names[code]
    except Exception:
        return f"unknown action {code!r}"
