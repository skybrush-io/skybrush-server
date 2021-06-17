from dataclasses import dataclass
from time import time
from trio import (
    CapacityLimiter,
    current_time,
    open_memory_channel,
    open_nursery,
    sleep,
    TooSlowError,
    WouldBlock,
)
from trio.lowlevel import ParkingLot
from trio_util import periodic
from typing import Optional, TYPE_CHECKING
from weakref import WeakKeyDictionary

from flockwave.server.ext.show.config import DroneShowConfiguration, StartMethod

from .packets import create_start_time_configuration_packet
from .types import MAVLinkMessageSpecification

__all__ = ("ScheduledTakeoffManager",)

if TYPE_CHECKING:
    from .network import MAVLinkNetwork


@dataclass
class TakeoffConfiguration:
    """Simple value object that encapsulates an optional desired start time as a UNIX
    timestamp, an authorization flag, and whether the start time should be
    updated on the drones or not.
    """

    #: Whether the swarm is authorized to start
    authorized: bool

    #: Whether the start time should be updated on the drones according to the
    #: takeoff_time property
    should_update_takeoff_time: bool = True

    #: The desired takeoff time of the swarm; `None` if the takeoff time should
    #: be cleared
    takeoff_time: Optional[int] = None

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

    def create_start_time_configuration_packet(self) -> MAVLinkMessageSpecification:
        return create_start_time_configuration_packet(
            start_time=self.takeoff_time,
            authorized=self.authorized,
            should_update_takeoff_time=self.should_update_takeoff_time,
        )


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

                    # Figure out what the desired takeoff time and the auth flag
                    # should be
                    takeoff_config = self._get_desired_takeoff_configuration(config)

                    # Broadcast a packet that contains the desired takeoff time
                    # and the auth flag. If it fails, well, it does not matter
                    # because we will check the UAVs one by one as well. We
                    # send this packet only if the start time is in the future.
                    if (
                        takeoff_config.takeoff_time is None
                        or takeoff_config.is_takeoff_in_the_future
                    ):
                        packet = takeoff_config.create_start_time_configuration_packet()
                        try:
                            await self._network.broadcast_packet(packet)
                        except Exception:
                            # Do not blow up if the broadcasting fails for any reason
                            pass

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

                        if (
                            takeoff_config.authorized
                            != uav.is_scheduled_takeoff_authorized
                        ):
                            # Auth flag is different so we definitely need an update
                            needs_update = True
                        elif takeoff_config.should_update_takeoff_time:
                            # Takeoff time must be cleared (None) or set to a specific
                            # value; we need an update if it is different from what
                            # we have on the UAV
                            needs_update = (
                                uav.scheduled_takeoff_time
                                != takeoff_config.takeoff_time
                            )
                        else:
                            # Auth flag is the same and the takeoff time does not
                            # need to change
                            needs_update = False

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
        async with open_nursery() as nursery:
            async with queue:
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
            async with self._limiter:  # type: ignore
                await self._update_start_time_on_uav_inner(uav)
        except TooSlowError:
            log = self._network.log
            if log:
                log.warn(
                    f"UAV {uav.id} did not respond to a start time configuration request"
                )
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
    # First we check whether the start has been authorized and update the
    # "authorized" flag on the swarm accordingly. If the start has been
    # authorized, we never mess around with the scheduled start time of the
    # drone. If the start has not been authorized, we clear the scheduled
    # start time of the drone.

    def _get_desired_takeoff_configuration(
        self, config: DroneShowConfiguration
    ) -> TakeoffConfiguration:
        """Returns the desired start time in seconds and the desired state
        of the takeoff authorization flag on all the UAVs.

        Returns a negative start time to indicate that the start time has to be
        left as is for each of the UAVs.
        """
        desired_auth_flag = config.authorized_to_start

        if config.start_method == StartMethod.AUTO:
            if config.start_time is not None:
                # User configured a start time so we want to set that
                return TakeoffConfiguration(
                    takeoff_time=int(config.start_time), authorized=desired_auth_flag
                )
            else:
                # User did not configure a start time so we want to clear what
                # there is on the drone
                return TakeoffConfiguration(authorized=desired_auth_flag)

        elif config.start_method == StartMethod.RC:
            if desired_auth_flag:
                # User authorized the start so we don't mess around with the
                # takeoff time, it is the responsibility of the person holding
                # the RC to set the takeoff time
                return TakeoffConfiguration(
                    authorized=True, should_update_takeoff_time=False
                )
            else:
                # User did not authorize the start yet so the start time must
                # be cleared
                return TakeoffConfiguration(authorized=False)

        else:
            return TakeoffConfiguration(authorized=False)

    async def _update_start_time_on_uav_inner(self, uav) -> None:
        takeoff_config = self._get_desired_takeoff_configuration(self._config)

        desired_auth_flag = takeoff_config.authorized
        desired_takeoff_time = takeoff_config.takeoff_time_in_legacy_format

        if (
            desired_takeoff_time is None or desired_takeoff_time >= 0
        ) and desired_takeoff_time != uav.scheduled_takeoff_time:
            await uav.set_scheduled_takeoff_time(seconds=desired_takeoff_time)

        if desired_auth_flag != uav.is_scheduled_takeoff_authorized:
            await uav.set_authorization_to_takeoff(desired_auth_flag)

        log = self._network.log
        if log:
            log.debug(f"Updating takeoff configuration of {uav.id}")

        # Remember that we sent a command to update the start time on this UAV
        # and that it was sent successfully so we don't try it again in the next
        # few seconds even if the status of the UAV is not updated yet from
        # another status packet
        self._uavs_last_updated_at[uav] = current_time()
