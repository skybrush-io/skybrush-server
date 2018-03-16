"""Extension that provides a clock named ``system`` in the Flockwave
server. The ``system`` clock always returns the current timestamp
according to the server, expressed as the number of seconds elapsed since
the Unix epoch, in UTC.
"""

from flockwave.server.errors import NotSupportedError
from flockwave.server.model import ClockBase


class SystemClock(ClockBase):
    """The system clock that the extension registers."""

    def __init__(self):
        """Constructor."""
        super(SystemClock, self).__init__(id="system", epoch=0)
        self._running = True

    def ticks_given_time(self, now):
        """Returns the number of clock ticks elapsed since the Unix epoch,
        assuming that the server clock reports that the current time is
        the one given in the 'now' argument.

        Parameters:
            now (float): the number of seconds elapsed since the Unix epoch,
                according to the internal clock of the server.

        Returns:
            float: the number of clock ticks elapsed
        """
        return now

    @ClockBase.ticks_per_second.setter
    def ticks_per_second(self, value):
        """Overrides the ``ticks_per_second`` setter to disallow setting
        the frequency of the clock.
        """
        raise NotSupportedError("this property is read-only")


clock = SystemClock()


def load(app, configuration, logger):
    """Loads the extension."""
    app.clock_registry.add(clock)


def unload(app, configuration):
    """Unloads the extension."""
    app.clock_registry.remove(clock)
