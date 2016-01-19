"""Model classes related to a single UAV."""

from __future__ import absolute_import

from datetime import datetime
from flockwave.spec.schema import get_uav_status_info_schema
from pytz import utc
from .metamagic import ModelMeta


__all__ = ("UAVStatusInfo", )


class UAVStatusInfo(object):
    """Class representing the status information available about a single
    UAV.
    """

    __metaclass__ = ModelMeta

    class __meta__:
        schema = get_uav_status_info_schema()

    def __init__(self, id=None, timestamp=None):
        """Constructor.

        Parameters:
            id (str or None): ID of the UAV
            timestamp (datetime or None): time when the status information
                was received. ``None`` means to use the current timestamp.
        """
        self.id = id
        if timestamp is None:
            # datetime.utcnow() is not okay here because it returns a
            # datetime object with tzinfo set to None. As a consequence,
            # isoformat() will not add the timezone information correctly
            # when the datetime object is formatted into JSON
            timestamp = utc.localize(datetime.now())
        assert timestamp.tzinfo is not None, \
            "UAV status information timestamp must be timezone-aware"
        self.timestamp = timestamp
        print(repr(self))
        print(repr(self._json))
