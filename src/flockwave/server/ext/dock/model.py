from flockwave.server.model.object import ModelObject

__all__ = ("Dock",)


class Dock(ModelObject):
    """Model object representing a docking station."""

    def __init__(self, id: str):
        """Constructor.

        Parameters:
            id: the ID of the docking station
        """
        self._id = id
        self._device_tree_node = None

    @property
    def device_tree_node(self):
        return self._device_tree_node

    @property
    def id(self):
        return self._id
