"""Implementation of classes and functions related to the continuous scanning
of a Crazyflie address space for Crazyflie drones.
"""

from functools import partial
from operator import attrgetter
from trio import MemorySendChannel, sleep
from typing import AsyncIterable, Callable, Iterable, List, Optional, Union

from aiocflib.drivers.crazyradio import RadioConfiguration
from flockwave.server.concurrency import aclosing

from .connection import CrazyradioConnection


__all__ = ("CrazyradioScannerTask",)


#: Typing for address list getters, i.e. functions that can be called with
#: no arguments and that return a list of Crazyradio addresses to scan
AddressListGetter = Callable[[], Iterable[str]]


async def create_default_schedule_for(
    addresses: Union[CrazyradioConnection, AddressListGetter],
    *,
    batch_size: int = 8,
    delay: float = 5,
) -> AsyncIterable[Optional[List[str]]]:
    """Creates a default scanning schedule for the given Crazyradio connection.

    The scanning schedule starts with a full scan, followed by partial scans
    of the address space such that there is at least 125 msec delay between
    consecutive partial scans, and there is at least 5000 msec delay between
    two consecutive _sets_ of partial scans such that a single set covers the
    entire address range.

    Parameters:
        batch_size: maximum number of addresses to scan in a single batch
        delay: number of seconds to wait between full scans of the address space
    """
    # If we have received a Crazyradio connection, create a getter for it
    if not callable(addresses):
        addresses = partial(attrgetter("address_space"), addresses)

    # Start with a full scan
    yield None

    # Wait before we can start with the first set of partial scans
    await sleep(delay)

    while True:
        # Do partial scans, making sure that only a given number of addresses
        # are placed in a single partial scan
        targets = []
        for address in addresses():
            targets.append(address)
            if len(targets) >= batch_size:
                # Slice full, scan it and then wait a bit
                yield targets
                del targets[:]
                await sleep(0.125)

        # If there is anything left in the last slice, scan it
        if targets:
            yield targets
            await sleep(0.125)

        # Wait until the next set of partial scans
        await sleep(max(delay - 0.125, 0))


async def scan_connection(
    conn: CrazyradioConnection,
    addresses: Union[CrazyradioConnection, AddressListGetter],
    scheduler: Callable[
        [Union[CrazyradioConnection, AddressListGetter]], AsyncIterable[Optional[List]]
    ] = create_default_schedule_for,
) -> AsyncIterable[Optional[List[RadioConfiguration]]]:
    """Asynchronous generator that scans the address space of a Crazyradio
    connection for Crazyflie drones and yields radio configuration objects
    for each drone that was discovered.

    Parameters:
        conn: the connection to scan
        addresses: optional callable that returns a list of addresses to scan
            when called with no arguments
        scheduler: a callable that can be called with a single connection and
            that will return an async generator that periodically yields lists
            of addresses to scan

    Yields:
        a RadioConfiguration instance for each drone that was discovered. You
        may send a truthy value back into the generator to indicate that you
        have acknowledged this item and you do not want the scanner to test for
        it in later scan attempts.
    """
    async for targets in scheduler(addresses or conn):
        result = await conn.scan(targets)
        for target in result:
            yield target


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
        self._excluded = set()
        self._log = log

    def _get_scannable_addresses(self) -> List[str]:
        """Returns a list containing all the addresses that should be scanned in
        a single full scan, excluding all the addresses for which we have already
        found a drone that is still turned on.
        """
        addresses = set(self._conn.address_space) - self._excluded
        return sorted(addresses)

    def _notify_uav_gone(self, uri: str) -> None:
        """Notifies the task that the UAV with the given URI is gone and we
        should resume scanning for it if it is still part of the address space.
        """
        self._excluded.discard(uri)

    async def run(self, channel: MemorySendChannel) -> None:
        """Implementation of the task itself.

        Parameters:
            channel: channel in which we should put the address space and index
                of any newly detected UAV
        """
        self._excluded = set()
        gen = scan_connection(self._conn, self._get_scannable_addresses)
        async with aclosing(gen):
            async for target in gen:
                target = target.to_uri()
                address_space = self._conn.address_space
                try:
                    index = address_space.index(target)
                except ValueError:
                    if self._log:
                        self._log.warn(
                            f"{target} not found in address space; this is most likely a bug"
                        )
                    index = -1

                if index >= 0:
                    disposer = partial(self._notify_uav_gone, target)
                    self._excluded.add(target)
                    await channel.send((address_space, index, disposer))
