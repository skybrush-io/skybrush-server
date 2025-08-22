from collections import Counter
from logging import getLogger
from random import random
from time import time
from typing import Iterable, Iterator, Optional

from pytest import fixture
from trio import CapacityLimiter, TooSlowError, sleep

from flockwave.server.ext.show.config import AuthorizationScope, DroneShowConfiguration
from flockwave.server.ext.show.takeoff import (
    ScheduledTakeoffManager,
    TakeoffConfiguration,
)
from flockwave.server.model.uav import UAVBase


class MockUAV(UAVBase):
    config: Optional[TakeoffConfiguration] = None

    def __init__(self, id: str) -> None:
        super().__init__(driver=None, id=id)


def create_rng() -> Iterator[float]:
    while True:
        yield random()


class MockScheduledTakeoffManager(ScheduledTakeoffManager[MockUAV]):
    broadcast_history: list[TakeoffConfiguration]
    unicast_history: list[tuple[MockUAV, TakeoffConfiguration]]

    _broadcast_enabled: bool = True
    """Whether broadcasting is enabled."""

    _crash_events: Counter[str]
    """Multiset of events that will crash the next time they are attempted."""

    _timeout_events: Counter[str]
    """Multiset of events that will time out the next time they are attempted."""

    _rng: Iterator[float]
    """Iterator that yields random numbers between 0 (inclusive) and 1 (exclusive)."""

    _unicast_enabled: bool = True
    """Whether unicast updates are enabled."""

    broadcast_success_probability: float = 1.0
    """Probability of a broadcast message reaching a drone successfully."""

    unicast_success_probability: float = 1.0
    """Probability of a unicast message reaching a drone successfully."""

    def __init__(
        self, uavs: Iterable[MockUAV], *, rng: Optional[Iterator[float]] = None
    ) -> None:
        super().__init__(log=getLogger("test_ext_show_takeoff"))
        self._uavs = list(uavs)
        self.broadcast_history = []
        self.unicast_history = []
        self._rng = rng or create_rng()
        self._crash_events = Counter()
        self._timeout_events = Counter()

    async def broadcast_takeoff_configuration(
        self, config: TakeoffConfiguration
    ) -> None:
        if self._broadcast_enabled:
            self.broadcast_history.append(config)
            for uav in self._uavs:
                if next(self._rng) < self.broadcast_success_probability:
                    uav.config = config

    def iter_uavs_to_schedule(self) -> Iterator[MockUAV]:
        if self._crash_events["iter_uavs_to_schedule"] > 0:
            self._crash_events["iter_uavs_to_schedule"] -= 1
            raise RuntimeError("Simulated crash")

        return iter(self._uavs)

    def uav_needs_update(self, uav: MockUAV, config: TakeoffConfiguration) -> bool:
        return self._unicast_enabled and uav.config != config

    async def update_uav(self, uav: MockUAV, config: TakeoffConfiguration) -> None:
        self.unicast_history.append((uav, config))

        if self._crash_events["update_uav"] > 0:
            self._crash_events["update_uav"] -= 1
            raise RuntimeError("Simulated crash")

        if self._timeout_events["update_uav"] > 0:
            self._timeout_events["update_uav"] -= 1
            raise TooSlowError("Simulated timeout")

        if not self._unicast_enabled:
            return

        if next(self._rng) < self.unicast_success_probability:
            uav.config = config

        # Pretend that a reconfiguration takes 0.1 seconds
        await sleep(0.1)

    # Functions to tweak the internal behaviour of the mock

    def disable_broadcast(self) -> None:
        self._broadcast_enabled = False

    def disable_unicast(self) -> None:
        self._unicast_enabled = False

    def trigger_crash_for_iteration(self, count: int = 1) -> None:
        self._crash_events["iter_uavs_to_schedule"] += count

    def trigger_crash_for_unicast(self, count: int = 1) -> None:
        self._crash_events["update_uav"] += count

    def trigger_timeout_for_unicast(self, count: int = 1) -> None:
        self._timeout_events["update_uav"] += count


@fixture
def config() -> DroneShowConfiguration:
    config = DroneShowConfiguration()
    config.update_from_json(
        {
            "start": {
                "authorized": True,
                "authorizationScope": "live",
                "method": "auto",
                "time": 1000,
                "clock": "show",
            }
        }
    )
    return config


@fixture
def uavs():
    return [MockUAV(id=f"uav{i}") for i in range(5)]


@fixture
def manager(uavs, config):
    manager = MockScheduledTakeoffManager(uavs)
    manager.notify_config_changed(config)
    return manager


no_takeoff_time = TakeoffConfiguration(
    takeoff_time=None,
    authorization_scope=AuthorizationScope.LIVE,
    should_update_takeoff_time=True,
)


async def test_scheduled_takeoff_manager_simple_broadcast(
    manager,
    nursery,
    autojump_clock,
):
    """Tests the scheduled takeoff manager's ability to broadcast takeoff
    configurations at regular intervals.
    """
    config = manager.config

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )
    after_revoked_auth = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.NONE,
        should_update_takeoff_time=True,
    )

    await nursery.start(manager.run)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.1)

    # No change registered yet because updates are triggered once per second
    assert manager.broadcast_history == [no_takeoff_time]

    # Wait for 2 more seconds, check that the updates were broadcast
    await sleep(2)
    assert manager.broadcast_history == [
        no_takeoff_time,
        got_takeoff_time,
        got_takeoff_time,
    ]
    manager.broadcast_history.clear()

    # Now revoke the authorization and see if the broadcast messages change
    config.update_from_json(
        {"start": {"authorized": False, "authorizationScope": "none"}}
    )
    manager.notify_config_changed(config)

    await sleep(3)
    assert manager.broadcast_history == [
        after_revoked_auth,
        after_revoked_auth,
        after_revoked_auth,
    ]


async def test_scheduled_takeoff_manager_simple_unicast(
    manager,
    nursery,
    autojump_clock,
):
    """Tests the scheduled takeoff manager's ability to configure drones with
    unicast messages if broadcasts are not available.
    """
    manager.disable_broadcast()

    config = manager.config

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )
    after_revoked_auth = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.NONE,
        should_update_takeoff_time=True,
    )

    await nursery.start(manager.run)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.1)

    # No broadcasts should have been sent until now
    assert manager.broadcast_history == []

    # All drones should have received a unicast reconfiguration request
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert uav.config == got_takeoff_time
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1

    # Wait for 2 more seconds, check that no more updates have been sent
    await sleep(2)
    assert len(manager.unicast_history) == 5
    manager.unicast_history.clear()

    # Now revoke the authorization and see if it triggers a reconfiguration
    config.update_from_json(
        {"start": {"authorized": False, "authorizationScope": "none"}}
    )
    manager.notify_config_changed(config)

    await sleep(1.1)

    # All drones should have received a unicast reconfiguration request again
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert uav.config == after_revoked_auth
        assert manager.unicast_history.count((uav, after_revoked_auth)) == 1


async def test_scheduled_takeoff_manager_unicast_rate_limiting(
    manager,
    nursery,
    autojump_clock,
):
    """Tests whether the scheduled takeoff manager properly limits the number of
    concurrent configuration requests.
    """
    manager.disable_broadcast()
    manager._limiter = CapacityLimiter(1)

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )

    await nursery.start(manager.run)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.15)

    # Only two drones should have received a unicast reconfiguration request
    # due to rate limiting (one reconfiguration takes 0.1 seconds)
    assert len(manager.unicast_history) == 2
    for _, uav_config in manager.unicast_history:
        assert uav_config == got_takeoff_time
    assert len({uav for uav, _ in manager.unicast_history}) == 2

    # Four drones should have received a unicast configuration request by now
    await sleep(0.2)
    assert len(manager.unicast_history) == 4
    for _, uav_config in manager.unicast_history:
        assert uav_config == got_takeoff_time
    assert len({uav for uav, _ in manager.unicast_history}) == 4

    # All drones should have received a unicast configuration request by now
    await sleep(0.2)
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert uav.config == got_takeoff_time
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1

    # Capacity limiter should be idle now
    assert manager._limiter.available_tokens == 1


async def test_scheduled_takeoff_manager_retries_unicast_messages(
    manager,
    nursery,
    autojump_clock,
):
    """Tests whether the scheduled takeoff manager attempts to retry unicast
    messages when they do not appear to go through.
    """
    manager.disable_broadcast()
    manager._limiter = CapacityLimiter(2)

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )

    # Simulate complete packet loss on the unicast link
    manager.unicast_success_probability = 0.0

    await nursery.start(manager.run)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.5)

    # Capacity limiter is limited to 2 concurrent requests, each request taking
    # 0.1 second, so in 0.5 seconds we should have had the chance to configure
    # 10 UAVs at most. However, since we are not attempting to configure the
    # same UAV twice in quick succession, we should see only 5 attempts in the
    # history, one for each UAV.
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        assert uav.config is None
    manager.unicast_history.clear()

    # Let's wait for the cooldown period to end and see if we get more attempts.
    # Note that since the cooldown period ends when update_uav() returns (i.e.
    # after 0.1 second in this unit test), and we try things once every second,
    # we need to wait a bit more. Attempts will start at T=4 (we are at T=0.5
    # now) and due to the capacity limiter, they will be dispatched by T=4.2 at
    # the earliest, so we test at T=4.3.
    await sleep(3.8)
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        assert uav.config is None
    manager.unicast_history.clear()

    # Now recover the unicast link. Again, due to the cooldown periods we need
    # to wait until T=8.3.
    manager.unicast_success_probability = 1.0
    await sleep(4.0)
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        assert uav.config == got_takeoff_time


async def test_scheduled_takeoff_manager_handles_unicast_errors_gracefully(
    manager,
    nursery,
    autojump_clock,
):
    """Tests whether the scheduled takeoff manager handles exceptions raised
    during a unicast message attempt gracefully.
    """
    manager.disable_broadcast()
    manager._limiter = CapacityLimiter(2)

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )

    await nursery.start(manager.run)

    # Configure the scheduled takeoff manager to fail the next two unicast
    # message attempts
    manager.trigger_crash_for_unicast(count=2)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.5)

    # We should have attempted an update on all five UAVs; however, two of them
    # should have failed
    failed_uavs = []
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        if uav.config is None:
            failed_uavs.append(uav)
        else:
            assert uav.config == got_takeoff_time
    assert len(failed_uavs) == 2
    manager.unicast_history.clear()

    # Wait a bit more; in the next round they should be configured successfully.
    # The next round will start at T=4 due to the cooldown period so we can
    # test at T=4.05
    await sleep(3.55)
    assert len(manager.unicast_history) == len(failed_uavs)
    for uav in failed_uavs:
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        assert uav.config == got_takeoff_time


async def test_scheduled_takeoff_manager_handles_unicast_timeouts_gracefully(
    manager,
    nursery,
    autojump_clock,
):
    """Tests whether the scheduled takeoff manager handles timeouts during a
    unicast message sending attempt gracefully.
    """
    manager.disable_broadcast()
    manager._limiter = CapacityLimiter(2)

    start_time = int(time()) + 60000

    got_takeoff_time = TakeoffConfiguration(
        takeoff_time=start_time,
        authorization_scope=AuthorizationScope.LIVE,
        should_update_takeoff_time=True,
    )

    await nursery.start(manager.run)

    # Configure the scheduled takeoff manager to fail the next two unicast
    # message attempts
    manager.trigger_timeout_for_unicast(count=2)

    # Change start time
    manager.notify_start_time_changed(start_time)
    await sleep(0.5)

    # We should have attempted an update on all five UAVs; however, two of them
    # should have timed out
    failed_uavs = []
    assert len(manager.unicast_history) == 5
    for uav in manager.iter_uavs_to_schedule():
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        if uav.config is None:
            failed_uavs.append(uav)
        else:
            assert uav.config == got_takeoff_time
    assert len(failed_uavs) == 2
    manager.unicast_history.clear()

    # Wait a bit more; in the next round they should be configured successfully.
    # The next round will start at T=4 due to the cooldown period so we can
    # test at T=4.05
    await sleep(3.55)
    assert len(manager.unicast_history) == len(failed_uavs)
    for uav in failed_uavs:
        assert manager.unicast_history.count((uav, got_takeoff_time)) == 1
        assert uav.config == got_takeoff_time


async def test_scheduled_takeoff_manager_restarts_when_crashes(
    manager, nursery, autojump_clock
):
    """Tests the scheduled takeoff manager's ability to restart after a crash."""
    await nursery.start(manager.run)
    assert len(manager.broadcast_history) == 1
    await sleep(0.1)

    manager.trigger_crash_for_iteration()

    # Manager is supposed to restart in 0.5 seconds after a crash -- but first
    # we need to wait 0.9 seconds for the next crash to happen. So we wait
    # 0.9 + 0.6 seconds in total and that should be enough
    await sleep(0.6)

    # No crash yet
    assert len(manager.broadcast_history) == 1
    await sleep(0.9)

    # Now the crash should have happened and the manager restarted.
    # We should have 3 entries now in the broadcast history: one that we have
    # checked above, one that happened right before the crash, and one that
    # happened _after_ the manager has restarted
    assert len(manager.broadcast_history) == 3
