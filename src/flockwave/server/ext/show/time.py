"""Classes corresponding to the time axis management of drone shows."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from logging import Logger
from typing import Generic, TypeAlias, TypeVar
from weakref import WeakKeyDictionary

from trio import (
    TASK_STATUS_IGNORED,
    CapacityLimiter,
    MemorySendChannel,
    TooSlowError,
    WouldBlock,
    current_time,
    open_memory_channel,
    open_nursery,
    sleep,
)
from trio.lowlevel import ParkingLot
from trio_util import periodic

from flockwave.server.model import UAV

__all__ = (
    "BinaryTimeAxisConfiguration",
    "TimeAxisConfigurationManager",
)


BinaryTimeAxisConfiguration: TypeAlias = bytes
"""Binary representation of a time axis configuration
to be sent to drones."""

TUAV = TypeVar("TUAV", bound="UAV")


class TimeAxisConfigurationManager(ABC, Generic[TUAV]):
    """Class that manages the time axis configuration process on a group of drones.

    The class provides the following facilities:

    - maintains a current time axis configuration
    - broadcasts messages containing the current time axis configuration
      periodically (typically at 1 Hz)
    - watches UAVs and sends individual configuration messages to them if they
      did not respond to broadcast requests in time

    This class is meant to serve as a base class for concrete implementations
    in the context of another extension. For instance, in the `mavlink`
    extension, each MAVLink network has an instance of this object that manages
    the time axis configuration updates for that given MAVLink network.
    """

    _config: BinaryTimeAxisConfiguration | None = None
    """The binary representation of the time axis configuration of the show,
    including causal time axis segments with different rates of time.
    """

    _limiter: CapacityLimiter
    """Capacity limiter that controls how many individual drones we are trying
    to configure at the same time if the broadcast messages did not reach them
    in time.
    """

    _log: Logger | None = None
    """The logger that the time axis configuration manager uses to log events."""

    _parking_lot: ParkingLot
    """Low-level task coordination primitive that manages the execution of
    background tasks performed by this object.
    """

    _uavs_to_update: set[TUAV]
    """Set of UAVs waiting to be updated individually when the time axis
    configuration of the show changes.
    """

    _uavs_last_updated_at: WeakKeyDictionary[TUAV, float]
    """Records the timestamps when UAVs were last updated on an individual
    basis. Used for rate-limiting the individual configuration requests.
    """

    unicast_cooldown_period: float = 3
    """Number of seconds to wait between consecutive attempts to configure a
    drone via unicast requests.
    """

    def __init__(
        self,
        *,
        log: Logger | None = None,
        capacity_limiter: CapacityLimiter | None = None,
    ):
        """Constructor.

        Parameters:
            log: the logger to use to log messages from this object
            capacity_limiter: the capacity limiter to use to limit the number of
            concurrent updates
        """
        self._log = log

        self._limiter = capacity_limiter or CapacityLimiter(5)
        self._parking_lot = ParkingLot()
        self._uavs_to_update = set()
        self._uavs_last_updated_at = WeakKeyDictionary()

    @abstractmethod
    async def broadcast_time_axis_configuration(
        self, config: BinaryTimeAxisConfiguration
    ) -> None:
        """Broadcasts a message that configures the time axis configuration for all UAVs.

        May be a no-op if broadcasts are not supported; in this case the manager
        will fall back to individual configuration requests.

        Exceptions from this method are caught and ignored by the manager.
        If you want to log them, add your own logging in the implementation of
        this method.
        """
        ...

    @abstractmethod
    def iter_uavs_to_schedule(self) -> Iterator[TUAV]:
        """Returns an iterator over the UAVs managed by this object that are
        to be updated on an individual basis if they do not receive the
        broadcast configuration packet or do not respond to it.

        May return an empty iterator if you do not want to support individual
        configuration for the UAVs.
        """
        ...

    @abstractmethod
    def uav_needs_update(self, uav: TUAV, config: BinaryTimeAxisConfiguration) -> bool:
        """Returns whether the given UAV needs to be updated if the desired
        time axis configuration is the one provided as `config`.

        May return False unconditionally if you do not want to support individual
        configuration for the UAVs.

        Args:
            uav: the UAV to check
            config: the desired time axis configuration to check against
        """
        ...

    @abstractmethod
    async def update_uav(self, uav: TUAV, config: BinaryTimeAxisConfiguration) -> None:
        """Updates the given UAV with the desired time axis configuration.

        This method is called by the manager when it needs to update a UAV
        individually. It should not block for too long, as it is called from
        a background task that processes multiple UAVs in parallel.
        """
        ...

    @property
    def config(self) -> BinaryTimeAxisConfiguration | None:
        return self._config

    def notify_time_axis_config_changed(
        self, config: BinaryTimeAxisConfiguration
    ) -> None:
        """Notifies the manager that the time axis configuration has
        changed.
        """
        self._config = config
        self._parking_lot.unpark_all()
        self._trigger_uav_updates_soon()

    async def run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        """Background task that checks the time axis configuration on the UAVs
        of this network regularly and updates them as needed.
        """
        while True:
            try:
                await self._run(task_status=task_status)
            except Exception:
                if self._log:
                    self._log.exception(
                        "Time axis configuration manager stopped unexpectedly, restarting..."
                    )

                # Ensure that we call task_status.started() only once
                task_status = TASK_STATUS_IGNORED
                await sleep(0.5)

    async def _run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        log = self._log

        async with open_nursery() as nursery:
            queue_tx: MemorySendChannel[TUAV] = await nursery.start(
                self._process_uavs_scheduled_for_updates
            )
            task_status.started()

            async with queue_tx:
                async for _ in periodic(1):
                    config = self._config
                    if not config:
                        # No time axis config yet, wait for one
                        await self._parking_lot.park()
                        continue

                    # TODO: should we limit broadcast any time?

                    # Broadcast a packet that contains the desired time axis
                    # configuration. If it fails, well, it does not matter
                    # because we will check the UAVs one by one as well.
                    try:
                        await self.broadcast_time_axis_configuration(config)
                    except Exception:
                        # Do not blow up if the broadcasting fails for any reason
                        pass

                    # First we scan the _uavs array and find all UAVs that need to
                    # be configured. The actual configuration will take place in a
                    # separate task to ensure that we don't block the entire process
                    # with a single UAV that takes too much time to configure
                    for uav in self.iter_uavs_to_schedule():
                        if uav in self._uavs_to_update:
                            # An update is already scheduled for this UAV so
                            # we can skip it for now
                            continue

                        if not self.uav_needs_update(uav, config):
                            # UAV does not need an update
                            continue

                        timestamp = self._uavs_last_updated_at.get(uav)
                        if (
                            timestamp
                            and current_time() - timestamp
                            < self.unicast_cooldown_period
                        ):
                            # We have tried updating this UAV recently so
                            # let's wait a bit more
                            continue

                        try:
                            queue_tx.send_nowait(uav)
                        except WouldBlock:
                            # Okay, doesn't matter, we'll try again in the next
                            # iteration
                            if log:
                                log.warning(
                                    "Cannot schedule UAV for an update, will try later",
                                    extra={"id": uav.id},
                                )
                        else:
                            self._uavs_to_update.add(uav)

    async def _process_uavs_scheduled_for_updates(
        self, *, task_status=TASK_STATUS_IGNORED
    ) -> None:
        """Task that reads the queue in which we put the UAVs scheduled for an
        update and processes them one by one by spawning further background
        tasks for it.
        """
        queue_tx, queue_rx = open_memory_channel[TUAV](1024)
        async with open_nursery() as nursery:
            async with queue_rx:
                task_status.started(queue_tx)
                async for uav in queue_rx:
                    nursery.start_soon(
                        self._process_single_uav_scheduled_for_update, uav
                    )

    async def _process_single_uav_scheduled_for_update(self, uav: TUAV) -> None:
        """Background task updates the time axis configuration on a single UAV.

        Many of these tasks may be executed in parallel when we are configuring
        UAVs.

        Parameters:
            uav: the UAV to configure
        """
        assert self._config is not None

        try:
            async with self._limiter:
                log = self._log
                if log:
                    log.debug(f"Updating time axis configuration of {uav.id}")

                config = self._config
                await self.update_uav(uav, config)
        except TooSlowError:
            log = self._log
            if log:
                log.warning(
                    f"UAV {uav.id} did not respond to a time axis configuration request"
                )
        except Exception:
            log = self._log
            if log:
                log.exception(
                    f"Unexpected exception while updating time axis configuration on UAV {uav.id}"
                )
        finally:
            # Remember that we sent a command to update the time axis configuration
            # on this UAV and that it was sent successfully so we don't try it
            # again in the next few seconds even if the status of the UAV is
            # not updated yet from another status packet
            self._uavs_last_updated_at[uav] = current_time()

            try:
                self._uavs_to_update.remove(uav)
            except KeyError:
                log = self._log
                if log:
                    log.warning(
                        f"UAV {uav.id} missing from _uavs_to_update, might be a bug"
                    )

    def _trigger_uav_updates_soon(self) -> None:
        """Ensures that new configurations get propagated to the UAVs as
        soon as possible.

        This is achieved by clearing the "last updated" timestamps of the UAVs;
        otherwise the manager would not try to update a UAV if it was updated
        recently in the last three seconds.
        """
        self._uavs_last_updated_at.clear()
