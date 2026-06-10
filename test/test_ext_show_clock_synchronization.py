from pytest import fixture, raises

from flockwave.server.ext.show.clock import ClockSynchronizationHandler, ShowClock

ClockPair = tuple[ShowClock, ShowClock]


class MockTime:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


@fixture
def clocks() -> ClockPair:
    return ShowClock(), ShowClock()


@fixture
def mock_time() -> MockTime:
    return MockTime()


@fixture
def handler(mock_time: MockTime) -> ClockSynchronizationHandler:
    return ClockSynchronizationHandler(current_time=mock_time)


@fixture
def handler_with_point_of_no_return(
    mock_time: MockTime,
) -> ClockSynchronizationHandler:
    return ClockSynchronizationHandler(
        point_of_no_return_seconds=-10,
        current_time=mock_time,
    )


def test_disable_and_stop(clocks: ClockPair, handler: ClockSynchronizationHandler):
    show_clock, _ = clocks
    show_clock.start_time = 100

    assert show_clock.running
    handler.secondary_clock = show_clock
    assert handler.secondary_clock is show_clock
    handler.disable_and_stop()
    assert not handler.enabled
    assert not show_clock.running

    show_clock.start_time = 120

    assert show_clock.running
    handler.secondary_clock = show_clock
    handler.disable_and_stop()
    assert not handler.enabled
    assert not show_clock.running


def test_synchronization_basic(clocks: ClockPair, handler: ClockSynchronizationHandler):
    primary, secondary = clocks

    now = 100
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

    now = 100
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

    now = 100
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

    now = 100
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


def test_clock_adjustment_without_point_of_no_return(
    clocks: ClockPair, handler: ClockSynchronizationHandler, mock_time: MockTime
):
    # Set up a primary/secondary clock pair and fix the current time so the
    # synchronization logic remains deterministic throughout the test.
    primary, secondary = clocks

    mock_time.value = 100
    primary.start_time = 100

    # Start synchronization without configuring a point of no return so every
    # primary-clock adjustment should be reflected on the secondary clock.
    handler.secondary_clock = secondary
    handler.synchronize_to(primary, 5)

    # Verify the initial synchronized state to establish the reference from
    # which later primary-clock adjustments will move the secondary clock.
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.ticks_given_time(100) == -50
    assert secondary.ticks_given_time(105) == 0

    # Move the primary clock so the synchronized secondary clock should also be
    # shifted, demonstrating that adjustments are propagated without restriction.
    primary.start_time = 90
    assert secondary.running
    assert secondary.start_time == 95
    assert secondary.ticks_given_time(100) == 50
    assert secondary.ticks_given_time(95) == 0

    # Move the primary clock back to its original state and verify that the
    # secondary clock follows again, confirming repeatable unrestricted updates.
    primary.start_time = 100
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.ticks_given_time(100) == -50


def test_clock_adjustment_before_point_of_no_return(
    clocks: ClockPair,
    handler_with_point_of_no_return: ClockSynchronizationHandler,
    mock_time: MockTime,
):
    # Set up a primary/secondary clock pair and fix the current time so the
    # synchronization logic remains deterministic throughout the test.
    handler = handler_with_point_of_no_return
    primary, secondary = clocks

    now = mock_time.value
    primary.start_time = now

    # Start synchronization with the secondary clock still before the point of
    # no return so later primary-clock adjustments are expected to propagate.
    handler.secondary_clock = secondary
    handler.synchronize_to(primary, 15)

    # Verify the initial synchronized state to establish that the secondary
    # clock is running at -15 seconds, which is still before the -10 threshold.
    assert secondary.running
    assert secondary.start_time == 115
    assert secondary.seconds_given_time(now) == -15
    assert secondary.ticks_given_time(now) == -150

    # Adjust the primary clock forward while the secondary clock is still before
    # the point of no return, and confirm that the handler updates the secondary
    # clock
    primary.start_time = now - 10
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.seconds_given_time(now) == -5
    assert secondary.ticks_given_time(now) == -50


def test_clock_adjustment_beyond_point_of_no_return(
    clocks: ClockPair,
    handler_with_point_of_no_return: ClockSynchronizationHandler,
    mock_time: MockTime,
):
    handler = handler_with_point_of_no_return
    primary, secondary = clocks

    mock_logger = MockLogger()

    now = mock_time.value
    primary.start_time = now

    # Configure the handler
    handler.secondary_clock = secondary
    handler.log = mock_logger  # type: ignore
    handler.synchronize_to(primary, 5)
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.ticks_given_time(now) == -50
    assert secondary.ticks_given_time(now + 5) == 0
    assert not mock_logger.messages

    # Now simulate an adjustment to the primary clock
    primary.start_time = now - 10
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.ticks_given_time(now + 5) == 0
    assert len(mock_logger.messages) == 1
    assert mock_logger.messages[0][0] == "warning"
    mock_logger.messages.clear()

    # Set the primary clock back to its original start time
    primary.start_time = now
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.ticks_given_time(now + 5) == 0
    assert len(mock_logger.messages) == 1
    assert mock_logger.messages[0][0] == "warning"
    mock_logger.messages.clear()

    # Stop the primary clock -- the secondary clock should also be stopped as
    # it is not an adjustment
    primary.start_time = None
    assert secondary.running
    assert secondary.ticks_given_time(now + 5) == 0
    assert len(mock_logger.messages) == 1
    assert mock_logger.messages[0][0] == "warning"


def test_primary_clock_stop_without_point_of_no_return(
    clocks: ClockPair, handler: ClockSynchronizationHandler, mock_time: MockTime
):
    # Set up a synchronized primary/secondary clock pair without a point of no
    # return so stopping the primary clock should immediately stop the secondary.
    primary, secondary = clocks

    now = mock_time.value
    primary.start_time = now

    handler.secondary_clock = secondary
    handler.synchronize_to(primary, 5)

    # Verify the initial synchronized state to establish that the secondary
    # clock is running before the primary clock is stopped.
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.seconds_given_time(now) == -5

    # Stop the primary clock and confirm that the secondary clock also stops,
    # matching the baseline behavior when no point of no return is configured.
    primary.start_time = None
    assert not secondary.running
    assert secondary.start_time is None
    assert secondary.ticks_given_time(now) == 0


def test_primary_clock_stop_before_point_of_no_return(
    clocks: ClockPair,
    handler_with_point_of_no_return: ClockSynchronizationHandler,
    mock_time: MockTime,
):
    # Set up a synchronized primary/secondary clock pair with a point of no
    # return, but keep the secondary clock before that threshold.
    handler = handler_with_point_of_no_return
    primary, secondary = clocks

    now = mock_time.value
    primary.start_time = now

    handler.secondary_clock = secondary
    handler.synchronize_to(primary, 15)

    # Verify the initial synchronized state to establish that the secondary
    # clock is still before the point of no return when the stop happens.
    assert secondary.running
    assert secondary.start_time == 115
    assert secondary.seconds_given_time(now) == -15

    # Stop the primary clock and confirm that the secondary clock also stops,
    # because stop events are propagated even when a point of no return exists.
    primary.start_time = None
    assert not secondary.running
    assert secondary.start_time is None
    assert secondary.ticks_given_time(now) == 0


def test_primary_clock_stop_after_point_of_no_return(
    clocks: ClockPair,
    handler_with_point_of_no_return: ClockSynchronizationHandler,
    mock_time: MockTime,
):
    # Set up a synchronized primary/secondary clock pair with a point of no
    # return and place the secondary clock beyond that threshold.
    handler = handler_with_point_of_no_return
    primary, secondary = clocks

    now = mock_time.value
    primary.start_time = now

    handler.secondary_clock = secondary
    handler.synchronize_to(primary, 5)

    # Verify the initial synchronized state to establish that the secondary
    # clock has already passed the point of no return before stopping.
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.seconds_given_time(now) == -5

    # Stop the primary clock and confirm that the secondary clock keeps on running
    # with an unchanged start time
    primary.start_time = None
    assert secondary.running
    assert secondary.start_time == 105
    assert secondary.seconds_given_time(now) == -5
