from weakref import WeakKeyDictionary
from trio import (
    CapacityLimiter,
    current_time,
    open_memory_channel,
    open_nursery,
    sleep,
    WouldBlock,
)
from trio.lowlevel import ParkingLot
from trio_util import periodic
from typing import Optional, Tuple, TYPE_CHECKING

from flockwave.server.ext.show.config import DroneShowConfiguration, StartMethod

__all__ = ("ScheduledTakeoffManager",)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


class ScheduledTakeoffManager:
    """Class that manages the automatic takeoff process on a single MAVLink
    network.
    """

    def __init__(self, network: "MAVLinkNetwork"):
        """Constructor.

        Parameters:
            network: the network whose automatic takeoff process this object
                manages
        """
        self._config = None  # type: Optional[DroneShowConfiguration]
        self._limiter = CapacityLimiter(5)
        self._network = network
        self._parking_lot = ParkingLot()
        self._uavs_to_update = set()
        self._uavs_last_updated_at = WeakKeyDictionary()

    def notify_config_changed(self, config):
        """Notifies the manager that the scheduled takeoff configuration has
        changed.
        """
        self._config = config
        self._parking_lot.unpark_all()

        # Ensure that the new configuration gets propagated to the UAVs as
        # soon as possible by clearing the timestamps; otherwise the manager
        # would not try an UAV if it was updated recently in the last three
        # seconds
        self._uavs_last_updated_at.clear()

    async def run(self) -> None:
        """Background task that checks the scheduled start times on the UAVs
        of this network regularly and updates them as needed.
        """
        log = self._network.log
        while True:
            try:
                await self._run(log)
            except Exception:
                if log:
                    log.exception(
                        "Scheduled takeoff manager stopped unexpectedly, restarting..."
                    )
                await sleep(0.5)

    async def _run(self, log) -> None:
        async with open_nursery() as nursery:
            queue_tx, queue_rx = open_memory_channel(1024)
            nursery.start_soon(self._process_uavs_scheduled_for_updates, queue_rx)

            async with queue_tx:
                async for _ in periodic(1):
                    config = self._config
                    if not config:
                        # No scheduled takeoff config yet, wait for one
                        await self._parking_lot.park()
                        continue

                    # First we scan the _uavs array and find all UAVs that need to
                    # be configured. The actual configuration will take place in a
                    # separate task to ensure that we don't block the entire process
                    # with a single UAV that takes too much time to configure
                    for uav in self._network.uavs():
                        if (
                            not uav.is_connected
                            or not uav.supports_scheduled_takeoff
                            or uav in self._uavs_to_update
                        ):
                            continue

                        (
                            desired_takeoff_time,
                            desired_auth_flag,
                        ) = self._get_desired_takeoff_time_and_auth_flag_for(
                            uav, config
                        )

                        needs_update = (
                            desired_takeoff_time != uav.scheduled_takeoff_time
                            or desired_auth_flag != uav.is_scheduled_takeoff_authorized
                        )

                        if needs_update:
                            timestamp = self._uavs_last_updated_at.get(uav)
                            if timestamp and current_time() - timestamp < 3:
                                # We have tried updating this UAV recently so
                                # let's wait a bit more
                                pass
                            else:
                                try:
                                    queue_tx.send_nowait(uav)
                                    self._uavs_to_update.add(uav)
                                except WouldBlock:
                                    # Okay, doesn't matter, we'll try again in the next
                                    # iteration
                                    if log:
                                        log.warn(
                                            "Cannot schedule UAV for an update, will try later",
                                            extra={
                                                "id": f"{self._network.id}:{uav.id}"
                                            },
                                        )

    async def _process_uavs_scheduled_for_updates(self, queue) -> None:
        """Task that reads the queue in which we put the UAVs scheduled for an
        update and processes them one by one by spawning further background
        tasks for it.
        """
        async with queue:
            async with open_nursery() as nursery:
                while True:
                    async for uav in queue:
                        nursery.start_soon(self._update_start_time_on_uav, uav)

    async def _update_start_time_on_uav(self, uav) -> None:
        """Background task updates the desired start time and automatic takeoff
        authorization on a single UAV.

        Many of these tasks may be executed in parallel when we are configuring
        UAVs.

        Parameters:
            uav: the UAV to configure
            limiter: a capacity limiter that is used to ensure that the number
                of UAVs that are being configured in parallel does not exceed a
                limit that the network can handle
        """
        try:
            async with self._limiter:
                await self._update_start_time_on_uav_inner(uav)
        except Exception:
            log = self._network.log
            if log:
                log.exception(
                    f"Unexpected exception while updating start time on UAV {uav.id}"
                )
        finally:
            try:
                self._uavs_to_update.remove(uav)
            except KeyError:
                log = self._network.log
                if log:
                    log.warn(
                        f"UAV {uav.id} missing from _uavs_to_update, might be a bug"
                    )

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
    # We clear any start time that was configured on any of the drones in
    # the swarm, except if the start time is in the near future (next 20
    # seconds) or the near past (previous 20 seconds), in which case we
    # assume that it was set by flicking the RC switch so we need to keep it.
    # We also check whether the start has been authorized and update the
    # "authorized" flag on the swarm accordingly.

    def _get_desired_takeoff_time_and_auth_flag_for(
        self, uav, config: DroneShowConfiguration
    ) -> Tuple[Optional[float], bool]:
        """Returns the desired start time in seconds and the desired state
        of the takeoff authorization flag on the given UAV by examining the
        state of the UAV and the drone show configuration.
        """
        if config.start_method == StartMethod.AUTO:
            if config.start_time is not None:
                desired_takeoff_time = int(config.start_time)
            else:
                desired_takeoff_time = None
            desired_auth_flag = config.authorized_to_start

        elif config.start_method == StartMethod.RC:
            # TODO(ntamas): don't mess around with any of the settings if the
            # current start time of the UAV is within +- 20 seconds; we assume
            # that it was set from the RC by the user and we shouldn't override
            # it at all.

            desired_takeoff_time = None
            desired_auth_flag = config.authorized_to_start

        return desired_takeoff_time, desired_auth_flag

    async def _update_start_time_on_uav_inner(self, uav) -> None:
        (
            desired_takeoff_time,
            desired_auth_flag,
        ) = self._get_desired_takeoff_time_and_auth_flag_for(uav, self._config)

        if desired_takeoff_time != uav.scheduled_takeoff_time:
            await uav.set_scheduled_takeoff_time(seconds=desired_takeoff_time)

        if desired_auth_flag != uav.is_scheduled_takeoff_authorized:
            await uav.set_authorization_to_takeoff(desired_auth_flag)

        log = self._network.log
        if log:
            log.info(f"Updating takeoff configuration of {uav.id}")

        # Remember that we sent a command to update the start time on this UAV
        # and that it was sent successfully so we don't try it again in the next
        # few seconds even if the status of the UAV is not updated yet from
        # another status packet
        self._uavs_last_updated_at[uav] = current_time()
