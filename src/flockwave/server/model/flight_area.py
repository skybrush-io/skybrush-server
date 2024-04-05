"""Flight area related data structures and functions for the server."""

from dataclasses import dataclass, field
from typing import Any, Optional

from flockwave.gps.vectors import GPSCoordinate

__all__ = (
    "FlightAreaConfigurationRequest",
    "FlightAreaPoint",
    "FlightAreaPolygon",
)


#: Type specification for points in the flight area
FlightAreaPoint = GPSCoordinate


@dataclass
class FlightAreaPolygon:
    """Flight area inclusion or exclusion in the form of a polygon."""

    points: list[FlightAreaPoint] = field(default_factory=list)
    is_inclusion: bool = True

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of a flight area polygon
        in absolute (geodetic) coordinates."""
        return {
            "isInclusion": self.is_inclusion,
            "points": [point.json for point in self.points],
        }


@dataclass
class FlightAreaConfigurationRequest:
    """Object representing a flight area configuration object that can be enforced
    on a drone.

    This is admittedly minimal for the time being. We can update it as we
    implement support for more complex flight areas. Things that are missing:

    - circular flight areas

    - selectively turning on/off certain flight area types
    """

    min_altitude: Optional[float] = None
    """Minimum altitude that the drone must maintain; `None` means not to
    change the minimum altitude requirement.
    """

    max_altitude: Optional[float] = None
    """Maximum altitude that the drone is allowed to fly to; `None` means not
    to change the maximum altitude limit.
    """

    polygons: Optional[list[FlightAreaPolygon]] = None
    """Inclusion and exclusion polygons in the flight area; `None` means not to
    update the polygons.
    """

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of the flight area configuration in
        absolute (geodetic) coordinates."""
        return {
            "version": 1,
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
            "polygons": (
                None
                if self.polygons is None
                else [polygon.json for polygon in self.polygons]
            ),
        }
