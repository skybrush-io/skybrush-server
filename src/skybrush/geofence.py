"""Temporary place for functions that are related to the processing of
Skybrush-related geofence specifications, until we find a better place for them.
"""

from typing import Dict

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    FlatEarthToGPSCoordinateTransformation,
)
from flockwave.server.model.geofence import (
    GeofenceConfigurationRequest,
    GeofencePolygon,
)
from flockwave.server.utils import optional_float

from .show import get_coordinate_system_from_show_specification

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

    # We need a minimum altitude anyway, so if we don't have one, let's assume
    # the ArduCopter default
    if result.min_altitude is None:
        result.min_altitude = -10

    # Parse polygons and rally points
    polygons = geofence.get("polygons", ())
    rally_points = geofence.get("rally_points", ())

    if polygons or rally_points:
        coordinate_system = get_coordinate_system_from_show_specification(show)
    else:
        coordinate_system = None

    if polygons:
        assert coordinate_system is not None
        result.polygons = [
            _parse_polygon(polygon, coordinate_system) for polygon in polygons
        ]

    # TODO(ntamas): parse rally points

    return result


def _parse_polygon(
    polygon: Dict, coordinate_system: FlatEarthToGPSCoordinateTransformation
) -> GeofencePolygon:
    """Parses a polygon from the geofence specification using the given
    local-to-global coordinate system and returns the parsed polygon.

    Parameters:
        polygon: the polygon specification to parse
        coordinate_system: the local-to-global coordinate system
    """
    is_inclusion = bool(polygon.get("isInclusion", False))
    points = polygon.get("points", ())

    if points and points[0] == points[-1]:
        points.pop()

    points = [
        coordinate_system.to_gps(FlatEarthCoordinate(point[0], point[1], 0))
        for point in points
    ]

    return GeofencePolygon(points, is_inclusion=is_inclusion)
