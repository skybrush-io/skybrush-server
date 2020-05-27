from typing import Any, Optional

from flockwave.server.model.devices import DeviceTreeMutator, ObjectNode
from flockwave.server.model.object import ModelObject

__all__ = ("Dock", "is_dock")


class Dock(ModelObject):
    """Model object representing a docking station."""

    def __init__(self, id: str):
        """Constructor.

        Parameters:
            id: the ID of the docking station
        """
        self._id = id
        self._channels = {}
        self._device_tree_node = ObjectNode()
        self._initialize_device_tree_node(self._device_tree_node)

    @property
    def device_tree_node(self):
        return self._device_tree_node

    @property
    def id(self):
        return self._id

    @property
    def json(self):
        return {}

    def _initialize_device_tree_node(self, node):
        thermometer = node.add_device("thermometer")
        self._channels["external_temperature"] = thermometer.add_channel(
            "external_temperature", type=float, unit="°C"
        )
        self._channels["internal_temperature"] = thermometer.add_channel(
            "internal_temperature", type=float, unit="°C"
        )

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


def is_dock(x: Any) -> bool:
    """Returns whether the given object is a docking station."""
    return isinstance(x, Dock)
