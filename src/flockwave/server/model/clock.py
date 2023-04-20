"""Clock-related model objects."""

from abc import ABC, abstractmethod, abstractproperty
from blinker import Signal
from datetime import datetime, timezone
from time import time
from typing import ClassVar, Optional, Union

__all__ = ("Clock", "ClockBase", "StoppableClockBase", "TimeElapsedSinceReferenceClock")


class Clock(ABC):
    """Interface specification for clock objects."""

    started: ClassVar[Signal] = Signal()
    """Signal that is sent when the clock is started."""

    stopped: ClassVar[Signal] = Signal()
    """Signal that is sent when the clock is stopped."""

    changed: ClassVar[Signal] = Signal()
    """Signal that is sent when the clock is adjusted. The signal will have a
    keyword argument named ``delta`` that contains the number of clock ticks
    that the clock was adjusted with. The argument is positive if the clock was
    adjusted forward and negative if the clock was adjusted backward.
    """

    @abstractproperty
    def epoch(self) -> Optional[float]:
        """The epoch of the clock, expressed as the number of seconds from
        the Unix epoch to the epoch of the clock, in UTC, or ``None`` if
        the clock has no epoch or an unknown epoch.
        """
        raise NotImplementedError

    @abstractproperty
    def id(self) -> str:
        """The identifier of the clock."""
        raise NotImplementedError

    @abstractproperty
    def json(self):
        """The JSON representation of the clock in a clock status message
        of the Flockwave protocol.
        """
        raise NotImplementedError

    @abstractproperty
    def running(self) -> bool:
        """Whether the clock is running."""
        raise NotImplementedError

    @property
    def seconds(self) -> float:
        """Returns the current timestamp of the clock, i.e. the number of
        _seconds_ elapsed since the epoch.
        """
        return self.ticks_given_time(time()) / self.ticks_per_second

    @property
    def ticks(self) -> float:
        """Returns the current tick count of the clock, i.e. the number of
        _ticks_ elapsed since the epoch.
        """
        return self.ticks_given_time(time())

    @abstractmethod
    def ticks_given_time(self, now: float) -> float:
        """Returns the timestamp of the clock, assuming that the internal
        clock of the server is set to the given time.

        Parameters:
            now: the current time according to the internal clock of the server,
                expressed as the number of seconds elapsed since the Unix epoch

        Returns:
            the timestamp of the clock
        """
        raise NotImplementedError

    @abstractproperty
    def ticks_per_second(self) -> float:
        """Returns the number of clock ticks per second (in wall clock
        time).
        """
        raise NotImplementedError


class ClockBase(Clock):
    """Abstract base class for clock objects."""

    _epoch: Optional[float]
    """The epoch of the clock, expressed as the number of seconds from the Unix
    epoch to the epoch of the clock, in UTC, or ``None`` if the clock has no
    epoch or an unknown epoch.
    """

    _id: str
    """The identifier of the clock."""

    def __init__(self, id: str, epoch: Optional[float] = None):
        """Constructor.

        Creates a new clock with the given ID and the given epoch.

        Parameters:
            id: the identifier of the clock
            epoch: the epoch of the clock, expressed as the number of seconds
                from the Unix epoch to the epoch of the clock, in UTC, or
                ``None`` if the clock has no epoch or an unknown epoch.
        """
        self._epoch = epoch
        self._id = id

    @property
    def epoch(self) -> Optional[float]:
        """The epoch of the clock."""
        return self._epoch

    @property
    def id(self) -> str:
        """The identifier of the clock."""
        return self._id

    @property
    def json(self):
        """The JSON representation of the clock in a CLK-INF message of the
        Flockwave protocol.
        """
        epoch = self.epoch
        ticks = self.ticks_per_second
        now = time()
        result = {
            "id": self.id,
            "retrievedAt": int(now * 1000),
            "ticks": self.ticks_given_time(now),
            "running": self.running,
        }
        if self._epoch is not None:
            result["epoch"] = self._format_epoch(epoch)
        if ticks != 1:
            result["ticksPerSecond"] = self.ticks_per_second
        return result

    def _format_epoch(self, epoch: Optional[float]) -> Optional[Union[str, datetime]]:
        """Returns a formatted copy of the epoch value as it should appear
        in the JSON output.

        Parameters:
            epoch: the epoch value to format

        Returns:
            the formatted version of the epoch
        """
        if epoch is None:
            return None
        elif epoch == 0:
            return "unix"
        else:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)


class StoppableClockBase(ClockBase):
    """Abstract base class for clock objects that can be stopped and
    started.
    """

    _running: bool
    """Whether the clock is running."""

    _ticks_per_second: int
    """Number of clock ticks per second."""

    def __init__(self, id: str, epoch: Optional[float] = None):
        """Constructor.

        Creates a new stoppable clock with the given ID and the given epoch.
        The clock is stopped by default.

        Parameters:
            id: the identifier of the clock
            epoch: the epoch of the clock, expressed as the number of seconds
                from the Unix epoch to the epoch of the clock, in UTC, or
                ``None`` if the clock has no epoch or an unknown epoch.
        """
        super().__init__(id, epoch=epoch)
        self._running = False
        self._ticks_per_second = 1

    @property
    def running(self) -> bool:
        """Returns whether the clock is running."""
        return self._running

    @running.setter
    def running(self, value: bool):
        """Sets whether the clock is running."""
        if self._running == value:
            return

        self._running = value

        if self._running:
            self.started.send(self)
        else:
            self.stopped.send(self)

    def start(self) -> None:
        """Starts the clock if it was not running yet."""
        self.running = True

    def stop(self) -> None:
        """Stops the clock if it was running."""
        self.running = False

    @property
    def ticks_per_second(self) -> int:
        """Returns the number of clock ticks per second (in wall clock
        time).
        """
        return self._ticks_per_second

    @ticks_per_second.setter
    def ticks_per_second(self, value: int) -> None:
        value = int(value)
        if value <= 0:
            raise ValueError("ticks per second must be positive")
        self._ticks_per_second = value


class TimeElapsedSinceReferenceClock(ClockBase):
    """Clock that shows the number of seconds elapsed since a given reference
    time instant.
    """

    _reference_time: Optional[float] = None
    """The reference time from which we measure the number of seconds elapsed;
    ``None`` if no reference time was set.
    """

    def ticks_given_time(self, now: float) -> float:
        """Returns the number of clock ticks elapsed since the reference time
        of the clock. Returns a negative number if the current time is before
        the reference time, or zero if there is no reference time yet.

        Parameters:
            now: the number of seconds elapsed since the UNIX epoch

        Returns:
            the number of clock ticks since the reference time, assuming 10
            ticks per second
        """
        return (
            (now - self._reference_time) * 10
            if self._reference_time is not None
            else 0.0
        )

    @property
    def running(self) -> bool:
        return self._reference_time is not None

    @property
    def reference_time(self) -> Optional[float]:
        """The reference time, in seconds since the UNIX epoch, or ``None``
        if no reference time has been specified yet.
        """
        return self._reference_time

    @reference_time.setter
    def reference_time(self, value: Optional[float]):
        if self._reference_time == value:
            return

        old_value = self._reference_time
        running = self.running

        self._reference_time = value

        if self.running != running:
            if self.running:
                self.started.send(self)
            else:
                self.stopped.send(self)
        else:
            if old_value is not None and value is not None:
                delta = (old_value - value) * self.ticks_per_second
            else:
                # should not happen
                delta = 0.0  # pragma: no cover
            self.changed.send(self, delta=delta)

    @property
    def ticks_per_second(self) -> int:
        return 10
