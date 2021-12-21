"""Extension that creates one or more virtual clock objects in the server.

Right now the clocks stay fixed at their epochs. Later on this extension may
provide support for starting or stopping the clocks.

Useful primarily for debugging purposes.
"""

from contextlib import ExitStack
from trio import sleep_forever

from flockwave.server.model.clock import ClockBase

__all__ = ()


class VirtualClock(ClockBase):
    """Virtual clock that always stays at its epoch."""

    def ticks_given_time(self, now):
        """Returns zero unconditionally.

        Returns:
            float: zero, no matter what the current time is
        """
        return 0.0

    @property
    def running(self):
        return False

    @property
    def ticks_per_second(self):
        return 10


async def run(app, configuration, logger):
    """Runs the main task of the extension."""
    use_clock = app.import_api("clocks").use_clock
    with ExitStack() as stack:
        for clock_id in configuration.get("ids", []):
            stack.enter_context(use_clock(VirtualClock(id=clock_id)))
        await sleep_forever()


dependencies = ("clocks",)
