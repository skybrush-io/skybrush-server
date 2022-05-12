"""Implementation of classes and functions related to the continuous scanning
of a Crazyflie address space for Crazyflie drones.
"""

from errno import ENODEV
from functools import partial
from time import monotonic
from trio import Event, MemorySendChannel, move_on_after, sleep
from typing import AsyncIterable, Callable, ClassVar, Iterable, List, Optional, Union

from flockwave.concurrency import aclosing
from flockwave.server.utils import longest_common_prefix

from .connection import CrazyradioConnection


__all__ = ("CrazyradioScannerTask",)


#: Typing for address list getters, i.e. functions that can be called with
#: a priority argument (0 for normal scans, 1 for scans that intend to find
#: drones with whom we have recently lost contact) and that return a list of
#: Crazyradio addresses to scan
AddressListGetter = Callable[[int], Iterable[str]]


def _get_all_addresses_in_connection(conn: CrazyradioConnection, priority: int):
    return conn.address_space


#: Typing for schedulers, o.e. async iterators that yield list of addresses to
#: scan or yield `None` to request a full scan of an address space
class Scheduler:
    """Interface specification for schedulers; these are essentially async
    iterators that yield lists of addresses to scan, or yield `None` to
    request a full scan of an address space.
    """

    async def run(
        self, addresses: Union[CrazyradioConnection, AddressListGetter]
    ) -> AsyncIterable[Optional[List[str]]]:
        """Runs the scheduler, yielding lists of addresses to scan, or yielding
        `None` when a full scan is requested.

        Parameters:
            addresses: a callable that returns a list of addresses to scan when
                called with a single priority argument, or a single Crazyradio
                connection, in which case the whole address space of the radio
                connection is scanned.
        """
        # If we have received a Crazyradio connection, create a getter for it
        if not callable(addresses):
            addresses = partial(_get_all_addresses_in_connection, addresses)

        async for item in self._run(addresses):
            yield item

    async def _run(
        self, addresses: AddressListGetter
    ) -> AsyncIterable[Optional[List[str]]]:
        raise NotImplementedError

    def wake_up(self) -> None:
        """Wakes up the scheduler and asks it to do the next scan as soon as
        possible.
        """
        pass


class DefaultScheduler(Scheduler):
    """Default scanning schedule for a Crazyradio connection.

    The scanning schedule starts with a full scan, followed by partial scans
    of the address space such that there is at least 125 msec delay between
    consecutive partial scans, and there is at least 5000 msec delay between
    two consecutive _sets_ of partial scans such that a single set covers the
    entire address range.
    """

    def __init__(self, batch_size: int = 8, delay: float = 5):
        """Constructor.

        Parameters:
            batch_size: maximum number of addresses to scan in a single batch
            delay: number of seconds to wait between full scans of the address space
        """
        self._batch_size = batch_size
        self._delay = delay
        self._next_full_scan_at = monotonic()
        self._speedup_factor = 10
        self._speedup_counter = 0
        self._wakeup_event = Event()

    async def _run(self, addresses: AddressListGetter):
        # Start with a full scan
        yield None

        while True:
            # Determine how much time we need to wait until the next full scan
            delay = self._delay
            if self._speedup_counter > 0:
                delay /= self._speedup_factor
                self._speedup_counter -= 1

            # Wait until the next set of partial scans, or until we receive a
            # request to do a scan again immediately
            with move_on_after(max(delay, 0)):
                await self._wakeup_event.wait()
                self._wakeup_event = Event()

            # Do partial scans, making sure that only a given number of addresses
            # are placed in a single partial scan. If we were woken up
            # explicitly, scan only those addresses that have a priority > 1
            targets = []
            full_scan = (
                self._speedup_counter <= 0 or monotonic() > self._next_full_scan_at
            )
            for address in addresses(1 if not full_scan else 0):
                targets.append(address)
                if len(targets) >= self._batch_size:
                    # Slice full, scan it and then wait a bit
                    yield targets
                    del targets[:]
                    await sleep(0.125)

            # If there is anything left in the last slice, scan it
            if targets:
                yield targets

            # Set a deadline for the next full scan if this was a full scan
            if full_scan:
                self._next_full_scan_at = monotonic() + self._delay

    def wake_up(self) -> None:
        """Wakes up the scheduler and asks it to do the next scan as soon as
        possible.

        After a wakeup call, this scheduler also switches to shorter delays
        between scans for the next 10 full scans.
        """
        self._wakeup_event.set()
        self._speedup_counter = 10


class CrazyradioScannerTask:
    """Class responsible for handling a single Crazyradio connection from the
    time it is opened to the time it is closed.
    """

    last_invocation_failed: ClassVar[bool] = False

    @classmethod
    async def create_and_run(
        cls, conn: CrazyradioConnection, channel: MemorySendChannel, *args, **kwds
    ):
        """Creates and runs a new connection handler for the given radio
        connection.
        """
        try:
            await CrazyradioScannerTask(conn, *args, **kwds).run(channel)
        finally:
            # If we get here (i.e. exited the scanner task normally), it means that
            # the radio was unplugged or there was a communication error with the
            # radio. The connection cannot detect this condition so we need to.
            cls.last_invocation_failed = True
            conn.notify_error()
            await conn.wait_until_disconnected()

    def __init__(self, conn: CrazyradioConnection, log=None):
        """Constructor.

        Parameters:
            conn: the connection that the task handles
        """
        self._conn = conn
        self._excluded = set()
        self._priorities = {}
        self._log = log

    def _get_priority_of_address(self, address: str) -> int:
        """Returns the priority of an address in the order in which they are
        scanned. This is used to prioritize re-scanning for UAVs with which we
        have lost connection recently.
        """
        return self._priorities.get(address, 0)

    def _get_scannable_addresses(self, min_priority: int) -> List[str]:
        """Returns a list containing all the addresses that should be scanned in
        a single full scan, excluding all the addresses for which we have already
        found a drone that is still turned on.
        """
        if min_priority > 0:
            addresses = set(k for k, v in self._priorities.items() if v >= min_priority)
        else:
            addresses = set(self._conn.address_space)

        addresses -= self._excluded
        result = sorted(
            list(addresses), key=self._get_priority_of_address, reverse=True
        )
        self._update_priorities()

        return result

    def _notify_uav_gone(self, uri: str, scheduler: Scheduler) -> None:
        """Notifies the task that the UAV with the given URI is gone and we
        should resume scanning for it if it is still part of the address space.

        Parameters:
            uri: the URI of the UAV that is now gone
            scheduler: the scheduler that currently decides which URIs should
                be scanned. It is used to prioritize the URI for subsequent
                scans to improve reconnection times
        """
        self._excluded.discard(uri)

        # Prioritize scanning for this URI for the next 10 full scans
        self._priorities[uri] = 10
        scheduler.wake_up()

    def _update_priorities(self) -> None:
        """Updates the priorities of addresses that we are going to scan for,
        decreasing the priority by 1 for each address that has a non-zero
        priority.
        """
        has_zero_priority = False
        for address, priority in self._priorities.items():
            if priority > 0:
                self._priorities[address] = priority - 1
            else:
                has_zero_priority = True

        if has_zero_priority:
            to_delete = [
                address
                for address, priority in self._priorities.items()
                if priority <= 0
            ]
            for address in to_delete:
                del self._priorities[address]

    async def run(self, channel: MemorySendChannel) -> None:
        """Implementation of the task itself.

        Parameters:
            channel: channel in which we should put the address space and index
                of any newly detected UAV
        """
        space = self._conn.address_space
        first_address = space[0]
        if len(space) > 1:
            last_address = space[len(space) - 1]
            prefix = longest_common_prefix((first_address, last_address))
            if prefix:
                last_address = "..." + last_address[len(prefix) :]
            address_space = f"{first_address} to {last_address}"
        else:
            address_space = first_address

        if self._log and not self.last_invocation_failed:
            self._log.info(f"Scanning Crazyflies from {address_space}")

        try:
            await self._run(channel)
        except Exception as ex:
            if not self.last_invocation_failed and self._log:
                if getattr(ex, "errno", 0) == ENODEV:
                    # libusb indicates that the radio may have been disconnected.
                    # This is something worth logging but not worth sending to
                    # telemetry servers. Also, we log it only once and do not log
                    # it again for subsequent unsuccessful scanning attempts until
                    # we scan at least once successfully again.
                    self._log.error(
                        f"Crazyradio scanning {address_space} was probably unplugged.",
                        extra={"telemetry": "ignore"},
                    )
                else:
                    self._log.error(
                        f"Task scanning {address_space} stopped unexpectedly."
                    )

    async def _run(self, channel: MemorySendChannel) -> None:
        self._excluded = set()

        scheduler = DefaultScheduler()
        gen = scheduler.run(self._get_scannable_addresses)

        async with aclosing(gen):
            async for targets in gen:
                # TODO(ntamas): we have a problem here; suspended Crazyflie
                # drones appear in the scan, but then their task bails out
                # immediately with a communication timeout when we try to
                # initialize the connection. This should be fixed so we handle
                # suspended drones gracefully.
                result = await self._conn.scan(targets)
                self.__class__.last_invocation_failed = False

                for target in result:
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
                        disposer = partial(self._notify_uav_gone, target, scheduler)
                        self._excluded.add(target)
                        await channel.send((address_space, index, disposer))
