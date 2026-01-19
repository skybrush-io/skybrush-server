"""Mixin classes for other model objects."""

from datetime import datetime

from flockwave.server.utils import get_current_unix_timestamp_msec, is_timezone_aware

__all__ = ("TimestampMixin",)


TimestampLike = datetime | int
"""Type specification for timestamps that we accept in a TimestampMixin."""


def _timestamplike_to_timestamp(timestamp: TimestampLike | None) -> int:
    if timestamp is None:
        return get_current_unix_timestamp_msec()
    elif isinstance(timestamp, datetime):
        assert is_timezone_aware(timestamp), "Timestamp must be timezone-aware"
        return int(round(timestamp.timestamp() * 1000))
    else:
        return int(timestamp)


class TimestampMixin:
    """Mixin for classes that support a timestamp property."""

    timestamp: int
    """The timestamp, expressed in milliseconds elapsed since the UNIX epoch."""

    def __init__(self, timestamp: TimestampLike | None = None):
        """Mixin constructor. Must be called from the constructor of the
        class where this mixin is mixed in.

        Parameters:
            timestamp: the initial timestamp. ``None`` means to use the current
                date and time. Integers mean the number of milliseconds elapsed
                since the UNIX epoch, in UTC.
        """
        self.update_timestamp(timestamp)

    @property
    def age_msec(self) -> int:
        """Returns the number of milliseconds elapsed since the last update of
        the timestamp.
        """
        return get_current_unix_timestamp_msec() - self.timestamp

    def get_age_msec_at(self, now: TimestampLike) -> int:
        """Returns the number of milliseconds elapsed since the last update of
        the timestamp, assuming that the current time is given in `now`.

        Args:
            now: the current timestamp
        """
        return _timestamplike_to_timestamp(now) - self.timestamp

    def update_timestamp(self, timestamp: TimestampLike | None = None) -> None:
        """Updates the timestamp of the object.

        Parameters:
            timestamp: the new timestamp; ``None`` means to use the current date
                and time.
        """
        self.timestamp = _timestamplike_to_timestamp(timestamp)
