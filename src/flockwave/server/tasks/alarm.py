"""Asynchronous task that watches a given clock in the data model and
calls a function when the clock reaches a given tick count.
"""

from contextlib import ExitStack
from flockwave.server.logger import log as base_log
from flockwave.server.model.clock import Clock
from flockwave.server.utils import clamp
from typing import Optional

from trio import open_memory_channel, move_on_after, sleep, WouldBlock

__all__ = ("wait_until",)

log = base_log.getChild("alarm")


class _Alarm:
    """Internal implementation of `wait_until()`."""

    def __init__(self, clock: Clock, seconds: float):
        self._clock = clock
        self._queue_tx, self._queue_rx = open_memory_channel(1024)
        self._seconds = seconds

    async def run(self, edge_triggered: bool = False):
        with ExitStack() as stack:
            for signal in ("started", "stopped", "changed"):
                stack.enter_context(
                    getattr(self._clock, signal).connected_to(
                        self._on_clock_event, sender=self._clock
                    )
                )

            await self._run(edge_triggered)

    def _on_clock_event(self, sender, **kwds):
        try:
            self._queue_tx.send_nowait("event")
        except WouldBlock:
            log.warning("Clock event dropped, this should not have happened")

    async def _run(self, edge_triggered: bool):
        clock, seconds = self._clock, self._seconds
        queue_rx = self._queue_rx

        running = clock.running

        if edge_triggered:
            while True:
                seconds_left = seconds - clock.seconds if running else None
                if seconds_left is None or seconds_left < 0:
                    await queue_rx.receive()
                else:
                    break

        while True:
            seconds_left = seconds - clock.seconds if running else None

            if seconds_left is None:
                # Clock is not running, wait until it starts to run
                message = await queue_rx.receive()
            elif seconds_left <= 0:
                # Time is up!
                return
            else:
                # If we have more than 0.1 seconds left, we wait the number of seconds
                # left minus 100 msec. We start busy-looping at 100 msec to ensure that
                # we can return as close after the deadline as possible.
                #
                # Also, we re-check the time once every minute in case the user
                # adjusted the clock of the computer.
                to_wait = clamp(seconds_left - 0.1, 0, 60)
                if to_wait > 0:
                    # Ordinary wait
                    message = None
                    with move_on_after(to_wait):
                        message = await queue_rx.receive()
                else:
                    # Busy-looping
                    try:
                        message = queue_rx.receive_nowait()
                    except WouldBlock:
                        message = None
                        await sleep(0)

            if message:
                # CHeck whether the clock is (still) running
                running = clock.running


async def wait_until(
    clock: Clock,
    seconds: Optional[float] = None,
    ticks: Optional[float] = None,
    edge_triggered: bool = False,
) -> None:
    """Asynchronous task that watches a given clock in the data model and
    blocks until the clock reaches a given tick count or a given number of
    seconds.

    The task may be "edge triggered" or "level triggered". When it is
    level triggered and the clock is already past the value we are waiting
    for, the task will return immediately. Otherwise, the task will wait until
    the clock is rewound to _before_ the moment we are waiting for.

    Parameters:
        clock: the clock to watch
        seconds: the number of seconds that should appear on the clock when
            this function unblocks
        ticks: the number of ticks that should appear on the clock when
            this function unblocks
        edge_triggered: whether the task is "edge triggered" or "level
            triggered"; see the explanation above
    """
    if ticks is None and seconds is None:
        raise RuntimeError("exactly one of 'ticks' and 'seconds' must be given")

    if ticks is not None and seconds is not None:
        raise RuntimeError("only one of 'ticks' and 'seconds' may be given")

    if seconds is None:
        seconds = ticks / clock.ticks_per_second

    # Check whether we need to return immediately
    seconds_left = seconds - clock.seconds
    if seconds_left <= 0 and not edge_triggered:
        return

    await _Alarm(clock, seconds).run(edge_triggered)
