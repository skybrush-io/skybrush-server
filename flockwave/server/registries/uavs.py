"""A registry that contains information about all the UAVs that the
server knows.
"""

__all__ = ("UAVRegistry",)

from blinker import Signal

from .base import RegistryBase


class UAVRegistry(RegistryBase):
    """Registry that contains information about all the UAVs seen by the
    server.

    The registry allows us to quickly retrieve information about an UAV
    by its identifier, update the status information of an UAV, or check
    when was the last time we have received information about an UAV. The
    registry is also capable of purging information about UAVs that have
    not been seen for a while.

    Attributes:
        added (Signal): signal that is sent by the registry when a new UAV
            has been added to the registry. The signal has a keyword
            argment named ``uav`` that contains the UAV that has just been
            added to the registry.

        removed (Signal): signal that is sent by the registry when a UAV
            has been removed from the registry. The signal has a keyword
            argument named ``uav`` that contains the UAV that has just been
            removed from the registry.
    """

    added = Signal()
    removed = Signal()

    def add(self, uav):
        """Registers a UAV with the given identifier in the registry.

        This function is a no-op if the UAV is already registered.

        Parameters:
            uav (UAV): the UAV to register

        Throws:
            KeyError: if the ID is already registered for a different UAV
        """
        old_uav = self._entries.get(uav.id, None)
        if old_uav is not None and old_uav != uav:
            raise KeyError("UAV ID already taken: {0!r}".format(uav.id))
        self._entries[uav.id] = uav
        self.added.send(self, uav=uav)

    def remove(self, uav):
        """Removes the given UAV from the registry.

        This function is a no-op if the UAV is not registered.

        Parameters:
            uav (UAV): the UAV to deregister

        Returns:
            UAV or None: the UAV that was deregistered, or ``None`` if the
                UAV was not registered
        """
        return self.remove_by_id(uav.id)

    def remove_by_id(self, uav_id):
        """Removes the UAV with the given ID from the registry.

        This function is a no-op if the UAV is not registered.

        Parameters:
            uav_id (str): the ID of the UAV to deregister

        Returns:
            UAV or None: the UAV that was deregistered, or ``None`` if the
                UAV was not registered
        """
        uav = self._entries.pop(uav_id)
        self.removed.send(self, uav=uav)
        return uav
