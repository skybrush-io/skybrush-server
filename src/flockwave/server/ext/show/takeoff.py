from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from logging import Logger
from time import time
from trio import (
    TASK_STATUS_IGNORED,
    CapacityLimiter,
    MemorySendChannel,
    current_time,
    open_memory_channel,
    open_nursery,
    sleep,
    TooSlowError,
    WouldBlock,
)
from trio.lowlevel import ParkingLot
from trio_util import periodic
from typing import Generic, Iterator, Optional, TypeVar
from weakref import WeakKeyDictionary

from flockwave.server.model import UAV

from .config import (
    AuthorizationScope,
    DroneShowConfiguration,
    StartMethod,
)

__all__ = ("ScheduledTakeoffManager", "TakeoffConfiguration")


@dataclass
class TakeoffConfiguration:
    """Simple value object that encapsulates an optional desired start time as a UNIX
    timestamp, an authorization scope, and whether the start time should be
    updated on the drones or not.
    """

    authorization_scope: AuthorizationScope
    """The scope of authorization for the start of the show."""

    should_update_takeoff_time: bool = True
    """Whether the start time should be updated on the drones according to the
    `takeoff_time` property of this object.
    """

    takeoff_time: Optional[int] = None
    """The desired takeoff time of the swarm; `None` if the takeoff time should
    be cleared. Ignored if `should_update_takeoff_time` is set to `False`.
    """

    @classmethod
    def from_show_config(
        cls, config: DroneShowConfiguration, start_time: Optional[float]
    ):
        """Returns the desired start time in seconds and the desired state
        of the takeoff authorization flag on all the UAVs.
        """
        # We need to decide whether to set or clear the start time of each drone,
        # and whether to set or clear the authorization flag.
        #
        # The rules are as follows:
        #
        # If the swarm is configured to start automatically
        # =================================================
        #
        # First we check whether there is a configured start time and if so, we
        # forward the start time to the swarm. If there is no configured start
        # time, we attempt to clear the start time on the swarm. At the same
        # time, we check whether the start has been authorized and update the
        # "authorized" flag on the swarm accordingly.
        #
        # If the swarm is configured to start with the RC
        # ===============================================
        #
        # First we check whether the start has been authorized and update the
        # "authorized" flag on the swarm accordingly. If the start has been
        # authorized, we never mess around with the scheduled start time of the
        # drone. If the start has not been authorized, we clear the scheduled
        # start time of the drone.

        desired_auth_scope = config.scope_iff_authorized

        if config.start_method == StartMethod.AUTO:
            if start_time is not None:
                # User has a show clock and the show clock has a scheduled
                # start time so we want to use that
                return cls(
                    takeoff_time=int(start_time), authorization_scope=desired_auth_scope
                )
            else:
                # User has no show clock or the show clock is stopped, so we
                # want to clear what there is on the drone
                return cls(authorization_scope=desired_auth_scope)

        elif config.start_method == StartMethod.RC:
            if desired_auth_scope is not AuthorizationScope.NONE:
                # User authorized the start so we don't mess around with the
                # takeoff time, it is the responsibility of the person holding
                # the RC to set the takeoff time
                return cls(
                    authorization_scope=desired_auth_scope,
                    should_update_takeoff_time=False,
                )
            else:
                # User did not authorize the start yet so the start time must
                # be cleared
                return cls(authorization_scope=desired_auth_scope)

        else:
            return cls(authorization_scope=AuthorizationScope.NONE)

    @property
    def authorized(self) -> bool:
        """Returns whether the takeoff is authorized to start (by any means,
        i.e. lights-only mode is also considered as being authorized).
        """
        return self.authorization_scope is not AuthorizationScope.NONE

    @property
    def is_takeoff_in_the_future(self) -> bool:
        """Returns whether the desired takeoff time is in the future."""
        return self.takeoff_time is not None and self.takeoff_time >= time()

    @property
    def takeoff_time_in_legacy_format(self) -> Optional[int]:
        """Returns the desired takeoff time in the legacy format we used in
        earlier versions of the code.

        Returns:
            -1 if the takeoff time should not be updated, `None` if the takeoff
            time should be cleared, or the real takeoff time otherwise
        """
        return self.takeoff_time if self.should_update_takeoff_time else -1


TUAV = TypeVar("TUAV", bound="UAV")


class ScheduledTakeoffManager(ABC, Generic[TUAV]):
    """Class that manages the automatic takeoff process on a group of drones.

    The class provides the following facilities:

    - maintains a current show configuration and takeoff time
    - broadcasts messages containing the current takeoff time and authorization
      scope periodically (typically at 1 Hz)
    - watches UAVs and sends individual configuration messages to them if they
      did not respond to broadcast requests in time

    This class is meant to serve as a base class for concrete implementations
    in the context of another extension. For instance, in the `mavlink`
    extension, each MAVLink network has an instance of this object that manages
    the takeoff for that given MAVLink network.
    """

    _config: Optional[DroneShowConfiguration] = None
    """The configuration of the show to start, including the start method,
    the clock that the start is synchronized to, the start time according to
    the given clock, and the list of UAVs to start.
    """

    _limiter: CapacityLimiter
    """Capacity limiter that controls how many individual drones we are trying
    to configure at the same time if the broadcast messages did not reach them
    in time.
    """

    _log: Optional[Logger] = None
    """The logger that the takeoff manager uses to log events."""

    _parking_lot: ParkingLot
    """Low-level task coordination primitive that manages the execution of
    background tasks performed by this object.
    """

    _start_time: Optional[float] = None
    """The start time of the show, expressed as the number of seconds since
    the UNIX epoch.
    """

    _uavs_to_update: set[TUAV]
    """Set of UAVs waiting to be updated individually when the start time or the
    start configuration of the show changes.
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
        log: Optional[Logger] = None,
        capacity_limiter: Optional[CapacityLimiter] = None,
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
    async def broadcast_takeoff_configuration(
        self, config: TakeoffConfiguration
    ) -> None:
        """Broadcasts a message that configures the start time and authorization
        scope for all UAVs.

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
    def uav_needs_update(self, uav: TUAV, config: TakeoffConfiguration) -> bool:
        """Returns whether the given UAV needs to be updated if the desired
        takeoff configuration is the one provided as `config`.

        May return False unconditionally if you do not want to support individual
        configuration for the UAVs.

        Args:
            uav: the UAV to check
            config: the desired takeoff configuration to check against
        """
        ...

    @abstractmethod
    async def update_uav(self, uav: TUAV, config: TakeoffConfiguration) -> None:
        """Updates the given UAV with the desired takeoff configuration.

        This method is called by the manager when it needs to update a UAV
        individually. It should not block for too long, as it is called from
        a background task that processes multiple UAVs in parallel.
        """
        ...

    @property
    def config(self) -> Optional[DroneShowConfiguration]:
        return self._config

    def notify_config_changed(self, config: DroneShowConfiguration) -> None:
        """Notifies the manager that the scheduled takeoff configuration has
        changed.
        """
        self._config = config
        self._parking_lot.unpark_all()
        self._trigger_uav_updates_soon()

    def notify_start_time_changed(self, start_time: Optional[float]) -> None:
        """Notifies the manager that the scheduled start time of the show has
        been changed. This is typically a side effect of the user adjusting the
        start time manually, but it may also be related to the adjustment of
        some other clock that the show clock is synchronized to.
        """
        self._start_time = start_time
        self._trigger_uav_updates_soon()

    async def run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        """Background task that checks the scheduled start times on the UAVs
        of this network regularly and updates them as needed.
        """
        while True:
            try:
                await self._run(task_status=task_status)
            except Exception:
                if self._log:
                    self._log.exception(
                        "Scheduled takeoff manager stopped unexpectedly, restarting..."
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
                        # No scheduled takeoff config yet, wait for one
                        await self._parking_lot.park()
                        continue

                    # Figure out what the desired takeoff time and the auth flag
                    # should be
                    # TODO(ntamas): this could be cached; the desired takeoff
                    # configuration should depend on 'config' only
                    takeoff_config = TakeoffConfiguration.from_show_config(
                        config, self._start_time
                    )

                    # Broadcast a packet that contains the desired takeoff time
                    # and the auth scope. If it fails, well, it does not matter
                    # because we will check the UAVs one by one as well. We
                    # send this packet only if the start time is in the future
                    # or if the takeoff time was cleared in the configuration.
                    if (
                        takeoff_config.takeoff_time is None
                        or takeoff_config.is_takeoff_in_the_future
                    ):
                        try:
                            await self.broadcast_takeoff_configuration(takeoff_config)
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

                        if not self.uav_needs_update(uav, takeoff_config):
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
        """Background task updates the desired start time and automatic takeoff
        authorization on a single UAV.

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
                    log.debug(f"Updating takeoff configuration of {uav.id}")

                takeoff_config = TakeoffConfiguration.from_show_config(
                    self._config, self._start_time
                )
                await self.update_uav(uav, takeoff_config)
        except TooSlowError:
            log = self._log
            if log:
                log.warning(
                    f"UAV {uav.id} did not respond to a takeoff configuration request"
                )
        except Exception:
            log = self._log
            if log:
                log.exception(
                    f"Unexpected exception while updating takeoff configuration on UAV {uav.id}"
                )
        finally:
            # Remember that we sent a command to update the takeoff configuration
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
