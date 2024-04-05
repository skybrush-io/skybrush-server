"""Temporary place for functions that are related to the processing of
Skybrush-related flight area specifications, until we find a better place for them.
"""

from typing import Dict, Optional, Sequence, Union

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    FlatEarthToGPSCoordinateTransformation,
    GPSCoordinate,
)
from flockwave.server.model.flight_area import (
    FlightAreaConfigurationRequest,
    FlightAreaPolygon,
)
from flockwave.server.utils import optional_float

from .specification import (
    get_coordinate_system_from_show_specification,
    is_coordinate_system_in_show_specification_geodetic,
)

__all__ = ("get_flight_area_configuration_from_show_specification",)


def get_flight_area_configuration_from_show_specification(
    show: Dict,
) -> FlightAreaConfigurationRequest:
    result = FlightAreaConfigurationRequest()

    flight_area = show.get("flightArea", None)
    if not flight_area:
        # Show contains no flight_area specification so nothing to configure, just
        # leave the request empty
        return result

    version = flight_area.get("version", 0)
    if version is None:
        raise RuntimeError("flight area specification must have a version number")
    if version != 1:
        raise RuntimeError("only version 1 flight areas are supported")

    result.max_altitude = optional_float(flight_area.get("maxAltitude"))
    result.min_altitude = optional_float(flight_area.get("minAltitude"))

    # Parse polygons
    polygons = flight_area.get("polygons", ())

    if polygons:
        if is_coordinate_system_in_show_specification_geodetic(show):
            coordinate_system = None
        else:
            coordinate_system = get_coordinate_system_from_show_specification(show)

        if polygons:
            result.polygons = [
                _parse_polygon(polygon, coordinate_system) for polygon in polygons
            ]

    return result


def _parse_points(
    points: Sequence[list[Union[int, float]]],
    coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation],
) -> list[GPSCoordinate]:
    """Parses a list of points from the flight area specification using the given
    optional local-to-global coordinate system and returns the parsed points.

    Parameters:
        points: the point specification to parse
        coordinate_system: the local-to-global coordinate system or `None` if
            the points are defined in geodetic coordinates
    """
    return [
        (
            GPSCoordinate.from_json(point[:2])  # [lat, lon] in [deg1e-7]
            if coordinate_system is None
            else coordinate_system.to_gps(FlatEarthCoordinate(point[0], point[1], 0))
        )  # [x, y] local coordinates as float
        for point in points
    ]


def _parse_polygon(
    polygon: Dict, coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation]
) -> FlightAreaPolygon:
    """Parses a polygon from the flight area specification using the given optional
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

    return FlightAreaPolygon(points, is_inclusion=is_inclusion)
