"""Representation of the outside world in which the flock of UAVs live."""

from flockwave.gps.vectors import GPSCoordinate
from typing import Any

__all__ = ("World",)


class World:
    """Representation of the outside world in which the flock of UAVs live.

    The world is essentially a spatial index containing arbitrary objects.
    Methods are provided to extract objects in the vicinity of a given
    coordinate, optionally filtered by the classes of these objects.

    TODO: no spatial index yet, but there will be if needed
    """

    _items: list[tuple[GPSCoordinate, Any]]

    def __init__(self):
        """Constructor.

        Creates an empty world with no objects.
        """
        self._items = []

    def add(self, obj: Any, location: GPSCoordinate) -> None:
        """Adds the given object at the given location.

        Parameters:
            obj: the object to add
            location: the location to add the object to. Altitudes will be ignored.
        """
        self._items.append((location, obj))
