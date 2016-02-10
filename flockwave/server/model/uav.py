"""Model classes related to a single UAV."""

from __future__ import absolute_import

from flockwave.spec.schema import get_complex_object_schema
from .metamagic import ModelMeta
from .mixins import TimestampMixin
from .vectors import GPSCoordinate


__all__ = ("UAVStatusInfo", )


class UAVStatusInfo(TimestampMixin):
    """Class representing the status information available about a single
    UAV.
    """

    __metaclass__ = ModelMeta

    class __meta__:
        schema = get_complex_object_schema("uavStatusInfo")

    def __init__(self, id=None, timestamp=None):
        """Constructor.

        Parameters:
            id (str or None): ID of the UAV
            timestamp (datetime or None): time when the status information
                was received. ``None`` means to use the current date and
                time.
        """
        TimestampMixin.__init__(self, timestamp)
        self.id = id
        self.position = GPSCoordinate()
