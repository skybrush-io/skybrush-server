"""Extension that provides a clock named ``system`` in the Skybrush
server. The ``system`` clock always returns the current timestamp
according to the server, expressed as the number of seconds elapsed since
the Unix epoch, in UTC.
"""

from flockwave.server.model import ClockBase


class SystemClock(ClockBase):
    """The system clock that the extension registers."""

    def __init__(self):
        """Constructor."""
        super().__init__(id="system", epoch=0)

    @property
    def running(self):
        return True

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

    @property
    def ticks_per_second(self):
        return 1


clock = SystemClock()


def load(app):
    """Loads the extension."""
    app.import_api("clocks").register_clock(clock)


def get_dependencies():
    """Returns the dependencies of this extension."""
    return ("clocks",)


def unload(app):
    """Unloads the extension."""
    app.import_api("clocks").unregister_clock(clock)
