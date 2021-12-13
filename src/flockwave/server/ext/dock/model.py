from typing import Any, Optional

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.devices import DeviceTreeMutator, ObjectNode
from flockwave.server.model.metamagic import ModelMeta
from flockwave.server.model.mixins import TimestampLike, TimestampMixin
from flockwave.server.model.object import ModelObject
from flockwave.spec.schema import get_complex_object_schema


__all__ = ("Dock", "is_dock")


class DockStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single
    docking station.
    """

    class __meta__:
        schema = get_complex_object_schema("dockStatusInfo")

    def __init__(
        self, id: Optional[str] = None, timestamp: Optional[TimestampLike] = None
    ):
        """Constructor.

        Parameters:
            id: ID of the docking station
            timestamp: time when the status information was received. ``None``
                means to use the current date and time. Integers represent
                milliseconds elapsed since the UNIX epoch.
        """
        TimestampMixin.__init__(self, timestamp)
        self.id = id
        self.position = GPSCoordinate()


class Dock(ModelObject):
    """Model object representing a docking station."""

    def __init__(self, id: str):
        """Constructor.

        Parameters:
            id: the ID of the docking station
        """
        self._channels = {}
        self._device_tree_node = ObjectNode()
        self._id = id
        self._status = DockStatusInfo(id=id)
        self._initialize_device_tree_node(self._device_tree_node)

    @property
    def device_tree_node(self):
        return self._device_tree_node

    @property
    def id(self) -> str:
        return self._id

    @property
    def status(self) -> DockStatusInfo:
        """Returns a DockStatusInfo_ object representing the status of the
        dock.
        """
        return self._status

    def update_status(self, position: Optional[GPSCoordinate] = None):
        """Updates the status information of the docking station.

        Parameters with values equal to ``None`` are ignored.

        Parameters:
            position: the position of the docking station. It will be cloned to
                ensure that modifying this position object from the caller will
                not affect the docking station itself.
        """
        if position is not None:
            self._status.position.update_from(position, precision=7)
        self._status.update_timestamp()

    def update_temperatures(
        self,
        mutator: DeviceTreeMutator,
        external: Optional[float] = None,
        internal: Optional[float] = None,
    ) -> None:
        """Updates the external or internal temperature of the docking
        station.

        Parameters:
            mutator: the mutator object that can be used to manipulate the
                device tree nodes
            external: the new external temperature; `None` if it is unchanged
            internal: the new internal temperature; `None` if it is unchanged
        """
        if external is not None:
            mutator.update(self._channels["external_temperature"], external)
        if internal is not None:
            mutator.update(self._channels["internal_temperature"], internal)

    def _initialize_device_tree_node(self, node):
        thermometer = node.add_device("thermometer")
        self._channels["external_temperature"] = thermometer.add_channel(
            "external_temperature", type=float, unit="°C"
        )
        self._channels["internal_temperature"] = thermometer.add_channel(
            "internal_temperature", type=float, unit="°C"
        )


def is_dock(x: Any) -> bool:
    """Returns whether the given object is a docking station."""
    return isinstance(x, Dock)
