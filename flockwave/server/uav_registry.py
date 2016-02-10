"""A registry that contains information about all the UAVs that the
server knows.
"""

__all__ = ("UAVRegistry", )

from .model import RegistryBase, UAVStatusInfo


class UAVRegistry(RegistryBase):
    """Registry that contains information about all the UAVs seen by the
    server.

    The registry allows us to quickly retrieve information about an UAV
    by its identifier, update the status information of an UAV, or check
    when was the last time we have received information about an UAV. The
    registry is also capable of purging information about UAVs that have
    not been seen for a while.
    """

    def update_uav_status(self, uav_id, position=None):
        """Updates the status information of the given UAV.

        Parameters:
            uav_id (str): the ID of the UAV to update
            position (GPSCoordinate): the position of the UAV. It will be
                cloned to ensure that modifying this position object from
                the caller will not affect the UAV itself.
        """
        try:
            uav_info = self.find_by_id(uav_id)
        except KeyError:
            uav_info = UAVStatusInfo(id=uav_id)
            self._entries[uav_id] = uav_info

        if position is not None:
            uav_info.position.update_from(position)

        uav_info.update_timestamp()
