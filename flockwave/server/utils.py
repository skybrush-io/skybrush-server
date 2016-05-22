"""Utility functions that do not fit elsewhere."""

from datetime import datetime
from pytz import utc


__all__ = ("datetime_to_unix_timestamp", "is_timezone_aware",
           "itersubclasses")


_unix_epoch = datetime.utcfromtimestamp(0).replace(tzinfo=utc)


def datetime_to_unix_timestamp(dt):
    """Converts a Python datetime object to a Unix timestamp, expressed in
    the number of seconds since the Unix epoch.

    The datetime object must be timezone-aware to avoid confusion with the
    time zones.

    Parameters:
        dt (datetime): the Python datetime object

    Returns:
        float: the time elapsed since the Unix epoch, in seconds

    Raises:
        ValueError: if the given datetime is not timezone-aware
    """
    if not is_timezone_aware(dt):
        raise ValueError("datetime object must be timezone-aware")
    return (dt - _unix_epoch).total_seconds()


def is_timezone_aware(dt):
    """Checks whether the given Python datetime object is timezone-aware
    or not.

    Parameters:
        dt (datetime): the Python datetime object

    Returns:
        bool: ``True`` if the given object is timezone-aware, ``False``
            otherwise
    """
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def itersubclasses(cls):
    """Iterates over all the subclasses of the given class in a depth-first
    manner.

    Parameters:
        cls (type): the (new-style) Python class whose subclasses we are
            iterating over

    Yields:
        type: the subclasses of the given class in DFS order, including
            the class itself.
    """
    queue = [cls]
    while queue:
        cls = queue.pop()
        yield cls
        queue.extend(cls.__subclasses__())
