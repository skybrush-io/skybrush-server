"""Clock that can be used to determine how much time is left until the start
of the show, or the elapsed time into the show if it is already running.
"""

from typing import Optional

from flockwave.server.model import ClockBase

__all__ = ("ShowClock",)


class ShowClock(ClockBase):
    """Clock that shows the number of seconds elapsed since the scheduled start
    of the drone show.
    """

    def __init__(self):
        """Constructor."""
        super().__init__(id="show", epoch=None)
        self._start_time = None

    def ticks_given_time(self, now):
        """Returns the number of clock ticks elapsed since the scheduled start
        of the show. If the current time is before the scheduled start, returns
        a negative number.

        Returns zero if there is no scheduled start time yet.

        Parameters:
            now (float): the number of seconds elapsed since the Unix epoch,
                according to the internal clock of the server.

        Returns:
            float: the number of seconds since the scheduled start of the show
        """
        if self._start_time is None:
            return 0.0
        else:
            return (now - self._start_time) * 10

    @property
    def running(self):
        return self._start_time is not None

    @property
    def start_time(self) -> Optional[float]:
        """The scheduled start time, in seconds since the UNIX epoch, or ``None``
        if no start time has been scheduled yet.
        """
        return self._start_time

    @start_time.setter
    def start_time(self, value: Optional[float]):
        if self._start_time == value:
            return

        running = self.running

        self._start_time = value

        if self.running != running:
            if self.running:
                self.started.send(self)
            else:
                self.stopped.send(self)
        else:
            self.changed.send(self)

    @property
    def ticks_per_second(self):
        return 10
