"""Temporary place for functions that are related to the processing of
Skybrush-related geofence specifications, until we find a better place for them.
"""

from typing import Dict, Optional, Sequence, Union

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    FlatEarthToGPSCoordinateTransformation,
    GPSCoordinate,
)
from flockwave.server.model.geofence import (
    GeofenceAction,
    GeofenceConfigurationRequest,
    GeofencePolygon,
)
from flockwave.server.utils import optional_float

from .specification import (
    get_coordinate_system_from_show_specification,
    is_coordinate_system_in_show_specification_geodetic,
)

__all__ = ("get_geofence_configuration_from_show_specification",)


def get_geofence_configuration_from_show_specification(
    show: Dict,
) -> GeofenceConfigurationRequest:
    result = GeofenceConfigurationRequest()

    geofence = show.get("geofence", None)
    if not geofence:
        # Show contains no geofence specification so nothing to configure, just
        # leave the request empty
        return result

    version = geofence.get("version", 0)
    if version is None:
        raise RuntimeError("geofence specification must have a version number")
    if version != 1:
        raise RuntimeError("only version 1 geofences are supported")

    result.enabled = bool(geofence.get("enabled", True))
    result.max_altitude = optional_float(geofence.get("maxAltitude"))
    result.max_distance = optional_float(geofence.get("maxDistance"))
    result.min_altitude = optional_float(geofence.get("minAltitude"))

    # Parse geofence action
    action = geofence.get("action")
    if action:
        try:
            result.action = GeofenceAction(action)
        except ValueError:
            raise RuntimeError(f"unknown geofence action: {action!r}") from None

    # Parse polygons and rally points
    polygons = geofence.get("polygons", ())
    rally_points = geofence.get("rallyPoints", ())

    if polygons or rally_points:
        if is_coordinate_system_in_show_specification_geodetic(show):
            coordinate_system = None
        else:
            coordinate_system = get_coordinate_system_from_show_specification(show)

        if polygons:
            result.polygons = [
                _parse_polygon(polygon, coordinate_system) for polygon in polygons
            ]

        if rally_points:
            result.rally_points = _parse_points(rally_points, coordinate_system)

    return result


def _parse_points(
    points: Sequence[list[Union[int, float]]],
    coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation],
) -> list[GPSCoordinate]:
    """Parses a list of points from the geofence specification using the given
    optional local-to-global coordinate system and returns the parsed points.

    Parameters:
        points: the point specification to parse
        coordinate_system: the local-to-global coordinate system or `None` if
            the points are defined in geodetic coordinates
    """
    return [
        GPSCoordinate.from_json(point[:2])  # [lat, lon] in [deg1e-7]
        if coordinate_system is None
        else coordinate_system.to_gps(
            FlatEarthCoordinate(point[0], point[1], 0)
        )  # [x, y] local coordinates as float
        for point in points
    ]


def _parse_polygon(
    polygon: Dict, coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation]
) -> GeofencePolygon:
    """Parses a polygon from the geofence specification using the given optional
    local-to-global coordinate system and returns the parsed polygon.

    Parameters:
        polygon: the polygon specification to parse
        coordinate_system: the local-to-global coordinate system or `None` if
            the polygon is defined in geodetic coordinates
    """
    is_inclusion = bool(polygon.get("isInclusion", False))
    points = polygon.get("points", ())

    if points and points[0] == points[-1]:
        points.pop()

    points = _parse_points(points, coordinate_system)

    return GeofencePolygon(points, is_inclusion=is_inclusion)
