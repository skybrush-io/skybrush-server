"""Extension that provides a clock named ``system`` in the Flockwave
server. The ``system`` clock always returns the current timestamp
according to the server, expressed as the number of seconds elapsed since
the Unix epoch, in UTC.
"""

from flockwave.server.errors import NotSupportedError
from flockwave.server.model import ClockBase
from time import time


class SystemClock(ClockBase):
    """The system clock that the extension registers."""

    def __init__(self):
        """Constructor."""
        super(SystemClock, self).__init__(id="system", epoch=0)
        self._running = True

    @property
    def ticks(self):
        """Returns the number of clock ticks elapsed since the Unix epoch
        according to the internal clock.
        """
        return time()

    @ClockBase.ticks_per_second.setter
    def ticks_per_second(self, value):
        """Overrides the ``ticks_per_second`` setter to disallow setting
        the frequency of the clock.
        """
        raise NotSupportedError("this property is read-only")


def load(app, configuration, logger):
    """Loads the extension."""
    app.clock_registry.add(SystemClock())
