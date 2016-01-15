"""A registry that contains information about all the UAVs that the
server knows.
"""

__all__ = ("UAVRegistry", )


class UAVRegistry(object):
    """Registry that contains information about all the UAVs seen by the
    server.

    The registry allows us to quickly retrieve information about an UAV
    by its identifier, update the status information of an UAV, or check
    when was the last time we have received information about an UAV. The
    registry is also capable of purging information about UAVs that have
    not been seen for a while.
    """

    def __init__(self):
        """Constructor."""
        self._uavs = {}

    def find_uav_by_id(self, uav_id):
        """Returns an UAV given its ID.

        Parameters:
            uav_id (str): the ID of the UAV to retrieve

        Returns:
            object: the UAV with the given ID

        Raises:
            KeyError: if the given ID does not refer to an UAV in the
                registry
        """
        return self._uavs[uav_id]

    @property
    def ids(self):
        """Returns an iterable that iterates over all the UAV identifiers
        that are known to the registry.
        """
        return sorted(self._uavs.keys())
