"""Mixin classes for other model objects."""

from datetime import datetime
from flockwave.server.utils import is_timezone_aware
from time import time
from typing import Optional, Union

__all__ = ("TimestampMixin",)


#: Type specification for timestamps that we accept in a TimestampMixin
TimestampLike = Union[datetime, int]


class TimestampMixin:
    """Mixin for classes that support a timestamp property."""

    def __init__(self, timestamp: Optional[TimestampLike] = None):
        """Mixin constructor. Must be called from the constructor of the
        class where this mixin is mixed in.

        Parameters:
            timestamp: the initial timestamp. ``None`` means to use the current
                date and time. Integers mean the number of milliseconds elapsed
                since the UNIX epoch, in UTC.
        """
        self.update_timestamp(timestamp)

    def update_timestamp(self, timestamp: Optional[TimestampLike] = None):
        """Updates the timestamp of the object.

        Parameters:
            timestamp (datetime or None): the new timestamp; ``None`` means
            to use the current date and time.
        """
        if timestamp is None:
            timestamp = int(round(time() * 1000))
        elif isinstance(timestamp, datetime):
            assert is_timezone_aware(timestamp), "Timestamp must be timezone-aware"
            timestamp = int(round(timestamp.timestamp() * 1000))
        else:
            timestamp = int(timestamp)
        self.timestamp = timestamp
