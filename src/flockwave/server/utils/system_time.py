"""Utility functions related to getting or adjusting the system time."""

from platform import system
from time import time

try:
    from time import CLOCK_REALTIME, clock_settime
except ImportError:
    clock_settime = CLOCK_REALTIME = None

from flockwave.server.errors import NotSupportedError

__all__ = (
    "can_set_system_time",
    "get_current_unix_timestamp_msec",
    "get_system_time_msec",
    "set_system_time_msec",
)


def can_set_system_time() -> bool:
    """Returns whether the current user is allowed to modify the system time."""
    if system() in ("Darwin", "Linux"):
        # Only root can modify the system time
        from os import geteuid

        return geteuid() == 0
    else:
        # TODO(ntamas): implement this for Windows -- will need PyWin32
        return False


def get_current_unix_timestamp_msec() -> int:
    """Returns the current UNIX timestamp in milliseconds as an integer."""
    return int(round(time() * 1000))


get_system_time_msec = get_current_unix_timestamp_msec


def set_system_time_msec(timestamp: float) -> None:
    """Sets the system time to the given UNIX timestamp.

    Parameters:
        timestamp: the timestamp to set, in milliseconds

    Raises:
        PermissionError: if the current user has no permission to modify the
            system time
    """
    if not can_set_system_time():
        raise PermissionError("Cannot modify system time; permission denied")

    try:
        if clock_settime is not None:
            clock_settime(CLOCK_REALTIME, timestamp / 1000)
        else:
            raise NotSupportedError("Not supported on this platform")

    except PermissionError:
        raise PermissionError("Cannot modify system time; permission denied") from None
