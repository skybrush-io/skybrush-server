from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union


__all__ = ("GPSFix", "GPSFixType", "ScaledLatLonPair")


ScaledLatLonPair = tuple[int, int]
"""Type specification for a latitude and longitude pair stored in [1e-7 deg]"""


class GPSFixType(IntEnum):
    """Known GPS fix types."""

    NO_GPS = 0
    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3
    DGPS = 4
    RTK_FLOAT = 5
    RTK_FIXED = 6
    STATIC = 7


#: Type alias for objects that can be used to update a GPSFix object
GPSFixLike = Union[int, GPSFixType, "GPSFix"]


@dataclass
class GPSFix:
    """Class representing basic GPS fix information of a single UAV."""

    type: GPSFixType = GPSFixType.NO_GPS
    """GPS fix type."""

    num_satellites: Optional[int] = None
    """Number of satellites."""

    horizontal_accuracy: Optional[float] = None
    """Horizontal accuracy in meters."""

    vertical_accuracy: Optional[float] = None
    """Vertical accuracy in meters."""

    @property
    def json(self):
        retval = [int(self.type)]
        optionals = [
            int(self.num_satellites) if self.num_satellites is not None else None,
            (
                int(round(self.horizontal_accuracy * 1000))
                if self.horizontal_accuracy is not None
                else None
            ),
            (
                int(round(self.vertical_accuracy * 1000))
                if self.vertical_accuracy is not None
                else None
            ),
        ]
        while optionals and optionals[-1] is None:
            del optionals[-1]

        return retval + optionals

    def update_from(self, other: GPSFixLike) -> None:
        """Updates this GPS fix object from another one. You may also specify a
        single GPSFixType_ as the input; in this case, the fix type will be
        updated and the number of satellites will be cleared.
        """
        if isinstance(other, int):
            self.type = GPSFixType(other)
            self.num_satellites = None
            self.horizontal_accuracy = None
            self.vertical_accuracy = None
        elif isinstance(other, GPSFixType):
            self.type = other
            self.num_satellites = None
            self.horizontal_accuracy = None
            self.vertical_accuracy = None
        else:
            self.type = other.type
            self.num_satellites = other.num_satellites
            self.horizontal_accuracy = other.horizontal_accuracy
            self.vertical_accuracy = other.vertical_accuracy
