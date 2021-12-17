from blinker import Signal
from typing import Any, Optional

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.metamagic import ModelMeta
from flockwave.server.model.mixins import TimestampLike, TimestampMixin
from flockwave.server.model.object import ModelObject
from flockwave.server.model.utils import optionally_scaled_by

from flockwave.spec.schema import get_complex_object_schema


__all__ = ("Beacon", "is_beacon")


class BeaconBasicProperties(metaclass=ModelMeta):
    """Class representing the basic properties of a single beacon that typically
    will not change over time.
    """

    class __meta__:
        schema = get_complex_object_schema("beaconBasicProperties")

    def __init__(self, id: Optional[str] = None, name: Optional[str] = None):
        """Constructor.

        Parameters:
            id: ID of the beacon
            name: the human-readable name of the beacon (if any)
        """
        self.id = id or ""
        self.name = name or id or ""


class BeaconStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single beacon."""

    class __meta__:
        schema = get_complex_object_schema("beaconStatusInfo")
        mappers = {"heading": optionally_scaled_by(10)}

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
        self.id = id or ""
        self.position: Optional[GPSCoordinate] = None
        self.heading: Optional[float] = None
        self.active = False


class Beacon(ModelObject):
    """Model object representing a beacon."""

    updated = Signal(doc="Signal sent whenever the beacon status was updated.")

    name: str

    def __init__(self, id: str, name: Optional[str] = None):
        """Constructor.

        Parameters:
            id: the ID of the beacon
            name: the human-readable name of the beacon (if any)
        """
        self._id = id
        self._basic_properties = BeaconBasicProperties(id=id, name=name)
        self._status = BeaconStatusInfo(id=id)

    @property
    def device_tree_node(self) -> None:
        return None

    @property
    def basic_properties(self) -> BeaconBasicProperties:
        """Returns a BeaconBasicProperties_ object representing the basic
        properties of the beacon that are not likely to change over time.
        """
        return self._basic_properties

    @property
    def id(self) -> str:
        return self._id

    @property
    def status(self) -> BeaconStatusInfo:
        """Returns a BeaconStatusInfo_ object representing the status of the
        beacon.
        """
        return self._status

    def update_status(
        self,
        position: Optional[GPSCoordinate] = None,
        heading: Optional[float] = None,
        active: Optional[bool] = None,
    ):
        """Updates the status information of the beacon.

        Parameters with values equal to ``None`` are ignored.

        Parameters:
            position: the position of the beacon. It will be cloned to ensure
                that modifying this position object from the caller will
                not affect the beacon itself.
            heading: the heading of the beacon, in degrees.
            active: whether the beacon is active (operational)
        """
        if position is not None:
            if self._status.position is None:
                self._status.position = GPSCoordinate()
            self._status.position.update_from(position, precision=7)
        if heading is not None:
            # Heading is rounded to 2 digits; it is unlikely that more
            # precision is needed and it saves space in the JSON
            # representation
            self._status.heading = round(heading % 360, 2)
        if active is not None:
            self._status.active = bool(active)
        self._status.update_timestamp()

        self.updated.send(self)


def is_beacon(x: Any) -> bool:
    """Returns whether the given object is a beacon."""
    return isinstance(x, Beacon)
