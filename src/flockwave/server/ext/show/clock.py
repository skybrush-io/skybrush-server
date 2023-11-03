"""Clock that can be used to determine how much time is left until the start
of the show, or the elapsed time into the show if it is already running.
"""

from contextlib import contextmanager
from time import time
from typing import Any, Iterator, Optional

from flockwave.server.model.clock import (
    Clock,
    TimeElapsedSinceReferenceClock,
)

__all__ = ("ShowClock", "ShowEndClock")


class ShowClock(TimeElapsedSinceReferenceClock):
    """Clock that shows the number of seconds elapsed since the scheduled start
    of the drone show.
    """

    def __init__(self):
        """Constructor."""
        super().__init__(id="show", epoch=None)

    @property
    def start_time(self) -> Optional[float]:
        """The scheduled start time, in seconds since the UNIX epoch, or ``None``
        if no start time has been scheduled yet.

        Same as the reference time; kept for compatibility purposes only.
        """
        return self.reference_time

    @start_time.setter
    def start_time(self, value: Optional[float]):
        self.reference_time = value


class ShowEndClock(TimeElapsedSinceReferenceClock):
    """Clock that shows the number of seconds elapsed since the scheduled end
    of the drone show.
    """

    def __init__(self):
        """Constructor."""
        super().__init__(id="end_of_show", epoch=None)


class ClockSynchronizationHandler:
    """Class that holds references to a primary and a secondary clock and that
    synchronizes the two clocks such that the secondary clock is running if and
    only if the primary clock is running and the offset between the two clocks
    is constant.
    """

    _enabled: bool = False

    _primary_clock: Optional[Clock] = None
    _secondary_clock: Optional[TimeElapsedSinceReferenceClock] = None

    _subscribed_clock: Optional[Clock] = None
    """The clock whose signals the handler is currently subscribed to. Updated
    dynamically when the enabled / disabled state or the primary clock changes.
    """

    _primary_seconds_for_zero_secondary_seconds: float = 0
    """Number of seconds on the primary clock that should correspond to zero
    seconds on the secondary clock.
    """

    @property
    def enabled(self) -> bool:
        """Whether the synchronization mechanism is enabled. It is guaranteed
        that the secondary clock will not be adjusted by the synchronization
        mechanism when it is disabled.
        """
        return self._enabled

    @property
    def primary_clock(self) -> Optional[Clock]:
        """The primary clock; ``None`` means that it has not been assigned yet and
        the secondary clock should be stopped.
        """
        return self._primary_clock

    @property
    def secondary_clock(self) -> Optional[TimeElapsedSinceReferenceClock]:
        """The secondary clock; ``None`` means that it has not been assigned
        yet.

        Changing the clock while the synchronization is active will _not_ reset
        the state of the old clock as it is deassigned, but it _will_ update the
        state of the new clock immediately.
        """
        return self._secondary_clock

    @secondary_clock.setter
    def secondary_clock(self, value: Optional[TimeElapsedSinceReferenceClock]) -> None:
        if self._secondary_clock == value:
            return

        self._secondary_clock = value
        self._update_secondary_clock()

    def disable(self) -> None:
        """Disables the synchronization mechanism, but does not make any changes
        to the configuration of the secondary clock.
        """
        self._primary_clock = None
        self._primary_seconds_for_zero_secondary_seconds = 0.0
        self._subscribe_to_or_unsubscribe_from_primary_clock()
        self._enabled = False

    def disable_and_stop(self) -> None:
        """Disables the synchronization mechanism and stops the secondary
        clock.
        """
        self.disable()
        if self._secondary_clock:
            self._secondary_clock.reference_time = None

    def synchronize_to(self, clock: Clock, seconds: float) -> None:
        """Enables the synchronization mechanism and attaches it to the given
        primary clock.

        Parameters:
            clock: the primary clock to synchronize to
            seconds: the number of seconds on the primary clock that should
                belong to zero seconds in the secondary clock
        """
        self._enabled = True
        self._primary_clock = clock
        self._primary_seconds_for_zero_secondary_seconds = seconds
        self._subscribe_to_or_unsubscribe_from_primary_clock()
        self._update_secondary_clock()

    @contextmanager
    def use_secondary_clock(
        self, clock: TimeElapsedSinceReferenceClock
    ) -> Iterator[None]:
        """Context manager that assigns the given clock as a secondary clock
        to the synchronization object when entering the context and that
        detaches the clock when exiting the context.
        """
        if self.secondary_clock is not None:
            raise RuntimeError(
                "no secondary clock must be attached to the synchronization "
                "handler yet"
            )

        self.secondary_clock = clock
        try:
            yield
        finally:
            self.secondary_clock = None

    def _calculate_desired_state_of_secondary_clock(
        self, now: float
    ) -> tuple[bool, Optional[float]]:
        """Calculates the desired state of the secondary clock, given the
        primary clock. Assumes that the synchronization mechanism is enabled.

        Parameters:
            now: the current timestamp

        Returns:
            a tuple containing whether the secondary clock should be running
            and the number of _seconds_ that the secondary clock should display
        """
        if not self._primary_clock:
            return (False, 0)

        ticks_on_primary = self._primary_clock.ticks_given_time(now)
        time_diff_sec = (
            ticks_on_primary / self._primary_clock.ticks_per_second
            - self._primary_seconds_for_zero_secondary_seconds
        )
        return (self._primary_clock.running, time_diff_sec)

    def _on_primary_clock_changed(self, sender: Any = None, **kwds):
        """Event handler that is called when the primary clock has been started,
        stopped, adjusted or reassigned.
        """
        self._update_secondary_clock()

    def _subscribe_to_or_unsubscribe_from_primary_clock(self):
        target_clock = self._primary_clock if self._enabled else None
        if target_clock == self._subscribed_clock:
            return

        if self._subscribed_clock:
            self._subscribed_clock.started.disconnect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )
            self._subscribed_clock.stopped.disconnect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )
            self._subscribed_clock.changed.disconnect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )

        self._subscribed_clock = target_clock

        if self._subscribed_clock:
            self._subscribed_clock.started.connect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )
            self._subscribed_clock.stopped.connect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )
            self._subscribed_clock.changed.connect(
                self._on_primary_clock_changed, sender=self._subscribed_clock  # type: ignore
            )

    def _update_secondary_clock(self) -> None:
        """Updates the start time (epoch) of the secondary clock, i.e. the
        time instant when the clock is supposed to show zero ticks. Does nothing
        if there is no secondary clock or if the synchronization handler is
        disabled.
        """
        if not self._enabled or self._secondary_clock is None:
            return

        now = time()
        (
            should_run,
            time_on_secondary_clock,
        ) = self._calculate_desired_state_of_secondary_clock(now)

        if time_on_secondary_clock is None:
            reference_time = None
        else:
            reference_time = now - time_on_secondary_clock

        self._secondary_clock.reference_time = reference_time if should_run else None
