"""Mixin classes for other model objects."""

from datetime import datetime
from pytz import utc
from flockwave.server.utils import is_timezone_aware

__all__ = ("TimestampMixin",)


class TimestampMixin(object):
    """Mixin for classes that support a timestamp property."""

    def __init__(self, timestamp=None):
        """Mixin constructor. Must be called from the constructor of the
        class where this mixin is mixed in.

        Parameters:
            timestamp (datetime or None): the initial timestamp; ``None``
            means to use the current date and time.
        """
        self.update_timestamp(timestamp)

    def update_timestamp(self, timestamp=None):
        """Updates the timestamp of the connection status information.

        Parameters:
            timestamp (datetime or None): the new timestamp; ``None`` means
            to use the current date and time.
        """
        if timestamp is None:
            # datetime.utcnow() alone is not okay here because it returns a
            # datetime object with tzinfo set to None. As a consequence,
            # isoformat() would not add the timezone information correctly
            # when the datetime object is formatted into JSON. That's why
            # we need to use datetime.now(utc)
            timestamp = datetime.now(utc)
        assert is_timezone_aware(
            timestamp
        ), "UAV status information timestamp must be timezone-aware"
        self.timestamp = timestamp
