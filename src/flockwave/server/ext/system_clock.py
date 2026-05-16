"""Extension that provides a clock named ``system`` in the Skybrush
server. The ``system`` clock always returns the current timestamp
according to the server, expressed as the number of seconds elapsed since
the Unix epoch, in UTC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trio import sleep_forever

from flockwave.server.ext.clocks import ClocksExtensionAPI
from flockwave.server.model import ClockBase

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


class SystemClock(ClockBase):
    """The system clock that the extension registers."""

    def __init__(self):
        """Constructor."""
        super().__init__(id="system", epoch=0)

    @property
    def running(self) -> bool:
        return True

    def ticks_given_time(self, now: float) -> float:
        """Returns the number of clock ticks elapsed since the Unix epoch,
        assuming that the server clock reports that the current time is
        the one given in the 'now' argument.

        Parameters:
            now: the number of seconds elapsed since the Unix epoch,
                according to the internal clock of the server.

        Returns:
            the number of clock ticks elapsed
        """
        return now

    @property
    def ticks_per_second(self) -> int:
        return 1


async def run(app: SkybrushServer):
    """Runs the extension."""
    clocks = app.import_api("clocks", ClocksExtensionAPI)
    with clocks.use_clock(SystemClock()):
        await sleep_forever()


dependencies = ("clocks",)
description = "System clock that always shows the current timestamp of the server"
schema = {}
