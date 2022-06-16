from flockwave.server.ext.show.clock import ClockSynchronizationHandler, ShowClock
from pytest import fixture
from time import time
from typing import Tuple


ClockPair = Tuple[ShowClock, ShowClock]


@fixture
def clocks() -> ClockPair:
    return ShowClock(), ShowClock()


@fixture
def handler() -> ClockSynchronizationHandler:
    result = ClockSynchronizationHandler()
    return result


def test_disable_and_stop(clocks: ClockPair, handler: ClockSynchronizationHandler):
    show_clock, _ = clocks
    show_clock.start_time = time()

    assert show_clock.running
    handler.secondary_clock = show_clock
    assert handler.secondary_clock is show_clock
    handler.disable_and_stop()
    assert not handler.enabled
    assert not show_clock.running

    show_clock.start_time = time()

    assert show_clock.running
    handler.secondary_clock = show_clock
    handler.disable_and_stop()
    assert not handler.enabled
    assert not show_clock.running


def test_synchronization_basic(clocks: ClockPair, handler: ClockSynchronizationHandler):
    primary, secondary = clocks

    now = time()
    primary.start_time = now

    assert primary.running
    assert not secondary.running

    handler.secondary_clock = secondary
    assert not secondary.running

    handler.synchronize_to(primary, primary.ticks_per_second * 10)
    assert secondary.running
    assert secondary.start_time == primary.start_time + 10
    assert secondary.ticks_given_time(now + 10) == 0
    assert secondary.ticks_given_time(now) == -10 * secondary.ticks_per_second


def test_sync_stop_start(clocks: ClockPair, handler: ClockSynchronizationHandler):
    primary, secondary = clocks

    now = time()
    primary.start_time = now
    handler.secondary_clock = secondary

    handler.synchronize_to(primary, primary.ticks_per_second * 10)
    assert secondary.running
    assert secondary.ticks_given_time(now + 10) == 0

    primary.start_time = None
    assert not secondary.running
    assert secondary.start_time is None

    primary.start_time = now - 5
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 0

    primary.start_time = now + 3
    assert secondary.running
    assert secondary.ticks_given_time(now) == -13 * secondary.ticks_per_second


def test_detach_primary_clock(clocks: ClockPair, handler: ClockSynchronizationHandler):
    primary, secondary = clocks

    now = time()
    primary.start_time = now
    handler.secondary_clock = secondary

    handler.synchronize_to(primary, primary.ticks_per_second * 10)
    assert handler.primary_clock is primary
    assert secondary.running

    handler.disable_and_stop()
    assert handler.primary_clock is None
    assert not secondary.running
    assert secondary.ticks_given_time(now) == 0

    primary.start_time = now - 5
    assert not secondary.running
    assert secondary.ticks_given_time(now) == 0
