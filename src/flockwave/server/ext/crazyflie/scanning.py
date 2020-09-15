"""Implementation of classes and functions related to the continuous scanning
of a Crazyflie address space for Crazyflie drones.
"""

from trio import MemorySendChannel, sleep
from typing import AsyncIterable, Callable, List, Optional

from aiocflib.drivers.crazyradio import RadioConfiguration
from flockwave.server.concurrency import aclosing

from .connection import CrazyradioConnection


__all__ = ("scan_connection", "CrazyradioScannerTask")


async def create_default_schedule_for(
    conn: CrazyradioConnection, *, slice_size: int = 8
) -> AsyncIterable[RadioConfiguration]:
    """Creates a default scanning schedule for the given Crazyradio connection.

    The scanning schedule starts with a full scan, followed by partial scans
    of the address space such that there is at least 125 msec delay between
    consecutive partial scans, and there is at least 5000 msec delay between
    two consecutive _sets_ of partial scans such that a single set covers the
    entire address range.
    """
    # Create the slices for the partial scans
    num_addresses = len(conn.address_space)
    slices = [
        [
            conn.address_space[i]
            for i in range(start, min(num_addresses, start + slice_size))
        ]
        for start in range(0, num_addresses, slice_size)
    ]

    # Start with a full scan
    yield None

    while True:
        # Do consecutive partial scans
        for targets in slices:
            yield targets
            await sleep(0.125)

        # Wait until the next set of partial scans
        await sleep(5 - 0.125)


async def scan_connection(
    conn: CrazyradioConnection,
    scheduler: Callable[
        [CrazyradioConnection], AsyncIterable[Optional[List]]
    ] = create_default_schedule_for,
) -> AsyncIterable[RadioConfiguration]:
    """Asynchronous generator that scans the address space of a Crazyradio
    connection for Crazyflie drones and yields radio configuration objects
    for each drone that was discovered.

    Parameters:
        conn: the connection to scan
        scheduler: a callable that can be called with a single connection and
            that will return an async generator that periodically yields lists
            of addresses to scan

    Yields:
        a RadioConfiguration instance for each drone that was discovered. You
        may send a truthy value back into the generator to indicate that you
        have acknowledged this item and you do not want the scanner to test for
        it in later scan attempts.
    """
    excluded = set()
    index = conn.address_space._index

    async for targets in scheduler(conn):
        if targets is not None:
            targets_to_scan = [target for target in targets if target not in excluded]
            result = await conn.scan(targets_to_scan)
        else:
            result = await conn.scan()

        for target in result:
            exclude = yield target
            if exclude:
                address = target.to_uri(index=index)
                excluded.add(address)


class CrazyradioScannerTask:
    """Class responsible for handling a single Crazyradio connection from the
    time it is opened to the time it is closed.
    """

    @classmethod
    async def create_and_run(
        cls, conn: CrazyradioConnection, channel: MemorySendChannel, *args, **kwds
    ):
        """Creates and runs a new connection handler for the given radio
        connection.
        """
        await CrazyradioScannerTask(conn, *args, **kwds).run(channel)

    def __init__(self, conn: CrazyradioConnection, log=None):
        """Constructor.

        Parameters:
            conn: the connection that the task handles
        """
        self._conn = conn
        self._log = log

    async def run(self, channel: MemorySendChannel) -> None:
        """Implementation of the task itself.

        Parameters:
            channel: channel in which we should put the address space and index
                of any newly detected UAV
        """
        from .scanning import scan_connection

        gen = scan_connection(self._conn)
        async with aclosing(gen):
            while True:
                target = await gen.__anext__()

                address_space = self._conn.address_space

                try:
                    index = address_space.index(target.to_uri())
                except ValueError:
                    if self._log:
                        self._log.warn(
                            f"{target.to_uri()} not found in address space; this is most likely a bug"
                        )
                    index = -1

                if index >= 0:
                    await channel.send((address_space, index))

                await gen.asend(True)
