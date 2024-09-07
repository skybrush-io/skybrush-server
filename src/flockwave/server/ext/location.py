"""Extension that provides the server with the concept of the physical location
of the server in geodetic coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from math import inf
from typing import Any, ClassVar, Optional

from blinker import Signal
from flockwave.gps.distances import haversine
from flockwave.gps.vectors import GPSCoordinate
from flockwave.logger import Logger

from flockwave.server.types import Disposer


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
    """Estimated accuracy of the location; ``None`` if unknown. Locations
    without an accuracy value are treated as approximate.
    """

    UNKNOWN: ClassVar[Location]

    def format(self) -> str:
        """Formats this location as a human-readable string."""
        if self.position is None:
            return "unknown"

        approx_marker = " (approximate)" if self.accuracy is None else ""
        return f"{self.position.format()}{approx_marker}"


Location.UNKNOWN = Location()

_log: Optional[Logger] = None

_location_candidates: dict[str, tuple[float, Location]] = {}
"""Location candidates submitted by other extensions, along with the priorities."""

_location: Optional[Location] = None
"""The physical location of the server; `None` if it has not been calculated
yet from the candidates submitted by other extensions.
"""

_location_changed_signal: Optional[Signal] = None
"""Signal to emit when the location chosen by the extension changes."""

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
    global _location, _location_priority, _last_location, _location_changed_signal

    if _location is None:
        _location, _location_priority = _validate_location()

        # If the location changed significantly (more than 10m), log the
        # new location
        if _distance_of_locations(_last_location, _location) > 10:
            _log_current_location()

        _last_location = _location

        if _location_changed_signal:
            _location_changed_signal.send(location=_location)

    return _location


def provide_location(key: str, location: Location, priority: int = 0) -> Disposer:
    """Callback for other extensions that can provide location information
    for the server itself.

    Args:
        key: a unique identifier of the extension or data source that provides
            the location. Earlier submissions with the same key will be
            overwritten.
        priority: the priority of the information. This extension will return
            the location with the highest priority among all locations submitted
            by other data sources.

    Returns:
        a function that may be used to revoke the location
    """
    global _location, _location_candidates

    _location_candidates[key] = priority, location

    if priority >= _location_priority:
        # Best location will change, invalidate the cached location
        _location = None
        get_location()  # to trigger an update

    return partial(_revoke_location, key)


def _log_current_location() -> None:
    """Logs the current location."""
    global _location, _log

    if _log is not None:
        # Do not show "Server location changed to unknown" messages -- it is
        # confusing to see the message when the server is shutting down
        if _location is not None and _location.position is not None:
            _log.info(f"Server location changed to {_location.format()}")


def _reset() -> None:
    """Resets the internal state of the extension by clearing all location
    candidates and invalidating the chosen location object.
    """
    global _location, _location_candidates, _location_priority

    _location_candidates.clear()
    _location = None
    _location_priority = -inf


def _revoke_location(key: str) -> None:
    """Revokes a location from the location candidates.

    Args:
        key: the unique identifier of the location to revoke
    """
    global _location, _location_candidates

    if key not in _location_candidates:
        return

    _, revoked_location = _location_candidates.pop(key)
    if revoked_location is _location:
        # Best location will change, invalidate the cached location
        _location = None
        get_location()  # to trigger an update


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
    global _fallback_location, _location_changed_signal, _log

    _log = log
    _fallback_location = _extract_fallback_location_from_configuration(
        configuration.get("fixed")
    )
    _location_changed_signal = app.import_api("signals").get("location:changed")

    _reset()


def unload():
    global _log, _location_changed_signal

    _reset()

    _location_changed_signal = None
    _log = None


dependencies = ("signals",)
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
