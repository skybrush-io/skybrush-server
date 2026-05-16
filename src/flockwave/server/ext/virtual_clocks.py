"""Extension that creates one or more virtual clock objects in the server.

Right now the clocks stay fixed at their epochs. Later on this extension may
provide support for starting or stopping the clocks.

Useful primarily for debugging purposes.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from trio import sleep_forever

from flockwave.server.ext.clocks import ClocksExtensionAPI
from flockwave.server.model.clock import ClockBase

if TYPE_CHECKING:
    from logging import Logger

    from flockwave.server.app import SkybrushServer

__all__ = ()


class VirtualClock(ClockBase):
    """Virtual clock that always stays at its epoch."""

    def ticks_given_time(self, now: float) -> float:
        """Returns zero unconditionally.

        Returns:
            zero, no matter what the current time is
        """
        return 0.0

    @property
    def running(self) -> bool:
        return False

    @property
    def ticks_per_second(self) -> int:
        return 10


async def run(app: SkybrushServer, configuration, logger: Logger):
    """Runs the main task of the extension."""
    clocks = app.import_api("clocks", ClocksExtensionAPI)
    with ExitStack() as stack:
        for clock_id in configuration.get("ids", []):
            stack.enter_context(clocks.use_clock(VirtualClock(id=clock_id)))
        await sleep_forever()


dependencies = ("clocks",)
