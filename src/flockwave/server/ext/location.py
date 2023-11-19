"""Extension that provides the server with the concept of the physical location
of the server in geodetic coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Any, ClassVar, Optional

from flockwave.gps.distances import haversine
from flockwave.gps.vectors import GPSCoordinate
from flockwave.logger import Logger
from flockwave.server.utils.formatting import format_gps_coordinate


@dataclass(frozen=True)
class Location:
    """The physical location of the server in geodetic coordinates, with an
    optional estimated accuracy in meters.
    """

    position: Optional[GPSCoordinate] = None
    """The geodetic coordinates corresponding to the location; ``None`` if
    unknown.
    """

    accuracy: Optional[float] = None
    """Estimated accuracy of the location; ``None`` if unknown."""

    UNKNOWN: ClassVar[Location]


Location.UNKNOWN = Location()

_log: Optional[Logger] = None

_location_candidates: dict[str, tuple[float, Location]] = {}
"""Location candidates submitted by other extensions, along with the priorities."""

_location: Optional[Location] = None
"""The physical location of the server; `None` if it has not been calculated
yet from the candidates submitted by other extensions.
"""

_location_priority: float = -inf
"""Priority of the currently chosen best location."""

_last_location: Optional[Location] = None
"""The last reported physical location of the server; `None` if no location was
reported yet. Used to decide whether we should communicate the change of the
location to the user in the logs.
"""

_fallback_location: Location = Location.UNKNOWN
"""Fixed, fallback location that can be configured in the settings of the
extension. This location (if any) will be returned by the extension if no
other data source provides a location to the server.
"""


def _distance_of_locations(
    first: Optional[Location], second: Optional[Location]
) -> float:
    first_pos = first.position if first is not None else None
    second_pos = second.position if second is not None else None
    if first_pos is None:
        return 0.0 if second_pos is None else inf
    if second_pos is None:
        return inf
    return haversine(first_pos, second_pos)


def get_location() -> Location:
    """Returns the current estimate of the location of the server.

    Callers of this function should consider the returned Location_ object as
    ephemeral. The identity of the "real" location object in this extension
    may change at any time.
    """
    global _location, _location_priority, _last_location

    if _location is None:
        _location, _location_priority = _validate_location()

        # If the location changed significantly (more than 10m), log the
        # new location
        if _distance_of_locations(_last_location, _location) > 10:
            _log_current_location()

        _last_location = _location

    return _location


def provide_location(key: str, location: Location, priority: int = 0) -> None:
    """Callback for other extensions that can provide location information
    for the server itself.

    Arguments:
        key: a unique identifier of the extension or data source that provides
            the location. Earlier submissions with the same key will be
            overwritten.
        location: the location information provided by the extension or data
            source.
        priority: the priority of the information. This extension will return
            the location with the highest priority among all locations submitted
            by other data sources.
    """
    global _location

    _location_candidates[key] = priority, location

    if priority >= _location_priority:
        # Best location will change, invalidate the cached location
        _location = None


def _log_current_location() -> None:
    """Logs the current location."""
    global _location, _log

    if _log is not None:
        if _location is None or _location.position is None:
            _log.warn("Server location became unknown")
        else:
            _log.info(
                "Server location changed to "
                + format_gps_coordinate(_location.position)
            )


def _reset() -> None:
    """Resets the internal state of the extension by clearing all location
    candidates and invalidating the chosen location object.
    """
    global _location, _location_candidates

    _location_candidates.clear()
    _location = None
    _location_priority = -inf


def _validate_location() -> tuple[Location, float]:
    """Chooses the physical location of the server from the candidates submitted
    by other extensions and data sources.
    """
    global _fallback_location

    if _location_candidates:
        best = max(_location_candidates.values())
        return best[1], best[0]
    else:
        return _fallback_location, -inf


def _extract_fallback_location_from_configuration(obj: Any) -> Location:
    """Handles the `fixed` key of the configuration object and extracts the
    position and accuracy from it into a Location_ object.
    """
    if not isinstance(obj, dict):
        return Location.UNKNOWN

    lat, lon, alt, acc = 0, 0, None, -1

    pos = obj.get("position")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        lat = float(pos[0])
        lon = float(pos[1])
        if len(pos) > 2:
            alt = float(pos[2]) if isinstance(pos[2], (int, float)) else None

    acc = obj.get("fixed")
    acc = float(acc) if isinstance(acc, (int, float)) else -1
    if acc < 0:
        acc = -1

    return Location(position=GPSCoordinate(lat, lon, amsl=alt), accuracy=acc)


def load(app, configuration: dict[str, Any], log: Logger):
    global _fallback_location, _log

    _log = log
    _fallback_location = _extract_fallback_location_from_configuration(
        configuration.get("fixed")
    )
    _reset()


def unload():
    global _log

    _reset()
    _log = None


dependencies = ()
description = "Provides the physical location of the server in geodetic coordinates for other extensions"
exports = {
    "get_location": get_location,
    "provide_location": provide_location,
}
schema = {
    "properties": {
        "fixed": {
            "title": "Use fixed location as fallback",
            "description": (
                "Fixed location that is reported by this extension when no other "
                "extension provides the server with a more accurate location."
            ),
            "type": "object",
            "properties": {
                "position": {
                    "title": "Position",
                    "description": "Use geodetic coordinates (latitude, longitude, altitude in meters)",
                    "minItems": 3,
                    "maxItems": 3,
                    "type": "array",
                    "format": "table",
                    "items": {"type": "number"},
                    "propertyOrder": 1000,
                },
                "accuracy": {
                    "title": "Accuracy",
                    "description": "Accuracy of the measured coordinates, in meters, if known",
                    "type": "number",
                    "minValue": 0,
                    "default": 1,
                    "propertyOrder": 2000,
                    "required": False,
                },
            },
            "propertyOrder": 3000,
            "required": False,
        },
    }
}
tags = "experimental"
