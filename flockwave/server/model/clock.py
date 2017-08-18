"""Clock-related model objects."""

from __future__ import absolute_import

from abc import ABCMeta, abstractmethod, abstractproperty
from blinker import Signal
from datetime import datetime
from future.utils import with_metaclass
from pytz import utc
from time import time


__all__ = ("Clock", "ClockBase", "StoppableClockBase")


class Clock(with_metaclass(ABCMeta, object)):
    """Interface specification for clock objects.

    Attributes:
        started (Signal): signal that is sent when the clock is started
        stopped (Signal): signal that is sent when the clock is stopped
        changed (Signal): signal that is sent when the clock is adjusted
            manually. It carries a single keyword argument named 'delta'
            that contains the number of ticks with which the clock was
            adjusted. It is positive if the clock was adjusted forward and
            negative if the clock was adjusted backward.
    """

    started = Signal()
    stopped = Signal()
    changed = Signal()

    @abstractproperty
    def epoch(self):
        """The epoch of the clock, expressed as the number of seconds from
        the Unix epoch to the epoch of the clock, in UTC, or ``None`` if
        the clock has no epoch or an unknown epoch.
        """
        raise NotImplementedError

    @abstractproperty
    def id(self):
        """The identifier of the clock."""
        raise NotImplementedError

    @abstractproperty
    def json(self):
        """The JSON representation of the clock in a clock status message
        of the Flockwave protocol.
        """
        raise NotImplementedError

    @abstractproperty
    def running(self):
        """Whether the clock is running."""
        raise NotImplementedError

    @property
    def ticks(self):
        """Returns the current timestamp of the clock, i.e. the number of
        ticks elapsed since the epoch.
        """
        return self.ticks_given_time(time())

    @abstractmethod
    def ticks_given_time(self, now):
        """Returns the timestamp of the clock, assuming that the internal
        clock of the server is set to the given time.

        Parameters:
            now (float): the current time according to the internal
                clock of the server, expressed as the number of seconds
                elapsed since the Unix epoch

        Returns:
            float: the timestamp of the clock
        """
        raise NotImplementedError

    @abstractproperty
    def ticks_per_second(self):
        """Returns the number of clock ticks per second (in wall clock
        time).
        """
        raise NotImplementedError


class ClockBase(Clock):
    """Abstract base class for clock objects."""

    def __init__(self, id, epoch=None):
        """Constructor.

        Creates a new clock with the given ID and the given epoch. The clock
        is stopped by default.

        Parameters:
            id (str): the identifier of the clock
            epoch (Optional[float]): the epoch of the clock, expressed as
                the number of seconds from the Unix epoch to the epoch of
                the clock, in UTC, or ``None`` if the clock has no epoch or
                an unknown epoch.
        """
        self._epoch = epoch
        self._id = id
        self._running = False
        self._ticks_per_second = 1

    @property
    def epoch(self):
        """The epoch of the clock."""
        return self._epoch

    @property
    def id(self):
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
            "retrievedAt": datetime.fromtimestamp(now, tz=utc),
            "timestamp": self.ticks_given_time(now),
            "running": self.running
        }
        if self._epoch is not None:
            result["epoch"] = self._format_epoch(epoch)
        if ticks != 1:
            result["ticksPerSecond"] = self.ticks_per_second
        return result

    @property
    def running(self):
        """Returns whether the clock is running."""
        return self._running

    @property
    def ticks_per_second(self):
        """Returns the number of clock ticks per second (in wall clock
        time).
        """
        return self._ticks_per_second

    @ticks_per_second.setter
    def ticks_per_second(self, value):
        value = int(value)
        if value <= 0:
            raise ValueError("ticks per second must be positive")
        self._ticks_per_second = value

    def _format_epoch(self, epoch):
        """Returns a formatted copy of the epoch value as it should appear
        in the JSON output.

        Parameters:
            epoch (Optional[float]): the epoch value to format

        Returns:
            str or datetime: the formatted version of the epoch
        """
        if epoch is None:
            return None
        elif epoch == 0:
            return "unix"
        else:
            return datetime.fromtimestamp(epoch, tz=utc)


class StoppableClockBase(ClockBase):
    """Abstract base class for clock objects that can be stopped and
    started.
    """

    @ClockBase.running.setter
    def running(self, value):
        """Sets whether the clock is running."""
        if self._running == value:
            return

        self._running = value

        if self._running:
            self.started.send(self)
        else:
            self.stopped.send(self)

    def start(self):
        """Starts the clock if it was not running yet."""
        self.running = True

    def stop(self):
        """Stops the clock if it was running."""
        self.running = False
