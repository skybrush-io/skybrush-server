"""Utility functions related to getting or adjusting the system time."""

from pathlib import Path
from platform import system
from subprocess import CalledProcessError, run
from time import time
from trio import to_thread

try:
    from time import CLOCK_REALTIME, clock_settime
except ImportError:
    clock_settime = CLOCK_REALTIME = None

from flockwave.server.errors import NotSupportedError

__all__ = (
    "can_set_system_time",
    "can_set_system_time_detailed",
    "can_set_system_time_detailed_async",
    "get_current_unix_timestamp_msec",
    "get_system_time_msec",
    "set_system_time_msec",
)


def can_set_system_time() -> bool:
    """Returns whether the current user is allowed to modify the system time."""
    return can_set_system_time_detailed()[0]


def can_set_system_time_detailed() -> tuple[bool, str]:
    """Returns whether the current user is allowed to modify the system time, and
    if not, provides a reason why the current user cannot do that.

    Returns:
        a tuple consisting of a yes/no answer and a reason. The reason string
        is empty if the user can modify the system time.
    """
    if system() in ("Darwin", "Linux"):
        # Deferred import because geteuid() is not available on Windows
        from os import geteuid

        # Only root can modify the system time
        if geteuid() != 0:
            return False, "Only the root user can modify the system time."

        # On Linux, also check whether the system is synchronizing the clock to
        # NTP. If it does, we cannot set it on our own because the time would
        # be set back immediately by systemd
        if Path("/usr/bin/timedatectl").is_file():
            try:
                proc = run(
                    ["/usr/bin/timedatectl", "show"],
                    timeout=3,
                    capture_output=True,
                    check=True,
                )
            except CalledProcessError:
                # Hmm, let's just assume that there is no NTP sync. In the worst
                # case, we allow the user to set the time when it is synced
                # back immediately afterwards
                pass
            else:
                for line in proc.stdout.split(b"\n"):
                    if line.startswith(b"NTP=yes"):
                        return (
                            False,
                            "System clock is synchronized to a time server with NTP.",
                        )

    else:
        # TODO(ntamas): implement this for Windows -- will need PyWin32
        return False, "Setting the system time is not supported on this platform."

    return True, ""


async def can_set_system_time_detailed_async() -> tuple[bool, str]:
    """Returns whether the current user is allowed to modify the system time, and
    if not, provides a reason why the current user cannot do that.

    Does not block the current task; delegates the work to a worker thread
    instead.

    Returns:
        a tuple consisting of a yes/no answer and a reason. The reason string
        is empty if the user can modify the system time.
    """
    return await to_thread.run_sync(
        can_set_system_time_detailed, abandon_on_cancel=True
    )


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
    allowed, reason = can_set_system_time_detailed()
    if not allowed:
        raise PermissionError(reason)

    try:
        if clock_settime is not None and CLOCK_REALTIME is not None:
            clock_settime(CLOCK_REALTIME, timestamp / 1000)
        else:
            raise NotSupportedError("Not supported on this platform")

    except PermissionError:
        raise PermissionError("Cannot modify system time; permission denied") from None

    # On Linux, if the system has a hardware clock, we need to sync the new
    # time back to the hardware clock
    if (
        system() == "Linux"
        and Path("/usr/sbin/hwclock").is_file()
        and Path("/dev/rtc").exists()
    ):
        try:
            run(
                ["/usr/sbin/hwclock", "-w"],
                timeout=3,
                check=True,
            )
        except CalledProcessError:
            raise PermissionError(
                "Cannot store updated time in the hardware clock"
            ) from None


async def set_system_time_msec_async(timestamp: float) -> None:
    """Sets the system time to the given UNIX timestamp in an asynchronous
    manner.

    Does not block the current task; delegates the work to a worker thread
    instead.

    Parameters:
        timestamp: the timestamp to set, in milliseconds

    Raises:
        PermissionError: if the current user has no permission to modify the
            system time
    """
    return await to_thread.run_sync(
        set_system_time_msec, timestamp, abandon_on_cancel=True
    )
