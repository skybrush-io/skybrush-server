"""Clock-related model objects."""

from __future__ import absolute_import

from abc import ABCMeta, abstractproperty
from datetime import datetime
from six import add_metaclass
from pytz import utc


__all__ = ("Clock", "ClockBase", "StoppableClockBase")


@add_metaclass(ABCMeta)
class Clock(object):
    """Interface specification for clock objects."""

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

    @abstractproperty
    def ticks(self):
        """Returns the current timestamp of the clock, i.e. the number of
        ticks elapsed since the epoch.
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
        result = {
            "id": self.id,
            "timestamp": self.ticks,
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
        self._running = value

    def start(self):
        """Starts the clock if it was not running yet."""
        self.running = True

    def stop(self):
        """Stops the clock if it was running."""
        self.running = False
