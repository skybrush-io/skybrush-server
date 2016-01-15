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

    @property
    def ids(self):
        """Returns an iterable that iterates over all the UAV identifiers
        that are known to the registry.
        """
        return sorted(self._uavs.keys())
