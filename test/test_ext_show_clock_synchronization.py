from flockwave.server.ext.show.clock import ClockSynchronizationHandler, ShowClock
from pytest import fixture, raises
from time import time


ClockPair = tuple[ShowClock, ShowClock]


@fixture
def clocks() -> ClockPair:
    return ShowClock(), ShowClock()


@fixture
def handler() -> ClockSynchronizationHandler:
    return ClockSynchronizationHandler()


@fixture
def handler_with_point_of_no_return() -> ClockSynchronizationHandler:
    return ClockSynchronizationHandler(point_of_no_return_seconds=-10)


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

    handler.synchronize_to(primary, 10)
    assert secondary.running
    assert secondary.start_time == primary.start_time + 10
    assert secondary.ticks_given_time(now + 10) == 0
    assert secondary.ticks_given_time(now) == -10 * secondary.ticks_per_second


def test_sync_stop_start(clocks: ClockPair, handler: ClockSynchronizationHandler):
    primary, secondary = clocks

    now = time()
    primary.start_time = now
    handler.secondary_clock = secondary

    handler.synchronize_to(primary, 10)
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

    handler.synchronize_to(primary, 10)
    assert handler.primary_clock is primary
    assert secondary.running

    handler.disable_and_stop()
    assert handler.primary_clock is None
    assert not secondary.running
    assert secondary.ticks_given_time(now) == 0

    primary.start_time = now - 5
    assert not secondary.running
    assert secondary.ticks_given_time(now) == 0


def test_detach_primary_clock_without_stopping_secondary_clock(
    clocks: ClockPair, handler: ClockSynchronizationHandler
):
    primary, secondary = clocks

    now = time()
    primary.start_time = now
    handler.secondary_clock = secondary

    handler.synchronize_to(primary, 10)
    assert handler.primary_clock is primary
    assert secondary.running

    handler.disable()
    assert handler.primary_clock is None
    assert secondary.running
    assert secondary.ticks_given_time(now) == -10 * secondary.ticks_per_second


def test_secondary_clock_context_manager(
    clocks: ClockPair, handler: ClockSynchronizationHandler
):
    assert handler.secondary_clock is None

    with handler.use_secondary_clock(clocks[1]):
        assert handler.secondary_clock is clocks[1]

    assert handler.secondary_clock is None

    try:
        with handler.use_secondary_clock(clocks[1]):
            assert handler.secondary_clock is clocks[1]
            raise RuntimeError("foo")
    except RuntimeError:
        pass

    assert handler.secondary_clock is None

    with handler.use_secondary_clock(clocks[1]):
        with raises(RuntimeError):
            with handler.use_secondary_clock(clocks[0]):
                pass


class MockLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", message))

    def error(self, message):
        self.messages.append(("error", message))

    def warning(self, message):
        self.messages.append(("warning", message))


def test_clock_adjustment_beyond_point_of_no_return(
    clocks: ClockPair, handler_with_point_of_no_return: ClockSynchronizationHandler
):
    handler = handler_with_point_of_no_return
    primary, secondary = clocks

    mock_logger = MockLogger()

    now = time()
    primary.start_time = now

    # Configure the handler
    handler.secondary_clock = secondary
    handler.log = mock_logger  # type: ignore
    handler.synchronize_to(primary, 5)
    assert secondary.running
    assert secondary.ticks_given_time(now) == -50
    assert secondary.ticks_given_time(now + 5) == 0
    assert not mock_logger.messages

    # Now simulate an adjustment to the primary clock
    primary.start_time = now - 10
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 0
    assert len(mock_logger.messages) == 1
    assert mock_logger.messages[0][0] == "warning"
    mock_logger.messages.clear()

    # Set the primary clock back to its original start time
    primary.start_time = now
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 0
    assert len(mock_logger.messages) == 1
    assert mock_logger.messages[0][0] == "warning"
    mock_logger.messages.clear()

    # Stop the primary clock -- the secondary clock should also be stopped as
    # it is not an adjustment
    primary.start_time = None
    assert not secondary.running
    assert secondary.ticks_given_time(now) == 0
    assert secondary.ticks_given_time(now + 5) == 0
    assert not mock_logger.messages

    # Simulate an adjustment to the primary clock again -- now it should work
    primary.start_time = now - 10
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 100

    # Disable the synchronization, then adjust the primary clock again
    handler.disable()
    primary.start_time = now
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 100
