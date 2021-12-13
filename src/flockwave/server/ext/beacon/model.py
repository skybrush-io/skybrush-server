from typing import Any, Optional

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.metamagic import ModelMeta
from flockwave.server.model.mixins import TimestampLike, TimestampMixin
from flockwave.server.model.object import ModelObject
from flockwave.spec.schema import get_complex_object_schema


__all__ = ("Beacon", "is_beacon")


class BeaconStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single beacon."""

    class __meta__:
        schema = get_complex_object_schema("beaconStatusInfo")

    def __init__(
        self, id: Optional[str] = None, timestamp: Optional[TimestampLike] = None
    ):
        """Constructor.

        Parameters:
            id: ID of the beacon
            timestamp: time when the status information was received. ``None``
                means to use the current date and time. Integers represent
                milliseconds elapsed since the UNIX epoch.
        """
        TimestampMixin.__init__(self, timestamp)
        self.id = id
        self.position = GPSCoordinate()


class Beacon(ModelObject):
    """Model object representing a beacon."""

    def __init__(self, id: str):
        """Constructor.

        Parameters:
            id: the ID of the beacon
        """
        self._id = id
        self._status = BeaconStatusInfo(id=id)

    @property
    def id(self) -> str:
        return self._id

    @property
    def status(self) -> BeaconStatusInfo:
        """Returns a BeaconStatusInfo object representing the status of the
        dock.
        """
        return self._status

    def update_status(self, position: Optional[GPSCoordinate] = None):
        """Updates the status information of the beacon.

        Parameters with values equal to ``None`` are ignored.

        Parameters:
            position: the position of the beacon. It will be cloned to ensure
                that modifying this position object from the caller will
                not affect the beacon itself.
        """
        if position is not None:
            self._status.position.update_from(position, precision=7)
        self._status.update_timestamp()


def is_beacon(x: Any) -> bool:
    """Returns whether the given object is a beacon."""
    return isinstance(x, Beacon)
