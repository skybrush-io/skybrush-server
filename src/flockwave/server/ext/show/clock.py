"""Clock that can be used to determine how much time is left until the start
of the show, or the elapsed time into the show if it is already running.
"""

from time import time
from typing import Any, Optional, Tuple

from flockwave.server.model import Clock, ClockBase

__all__ = ("ShowClock",)


class ShowClock(ClockBase):
    """Clock that shows the number of seconds elapsed since the scheduled start
    of the drone show.
    """

    _start_time: Optional[float]
    """The scheduled start time of the show, expressed as the number of seconds
    elapsed since the UNIX epoch. ``None`` if no start time was set yet.
    """

    def __init__(self):
        """Constructor."""
        super().__init__(id="show", epoch=None)
        self._start_time = None

    def ticks_given_time(self, now: float) -> float:
        """Returns the number of clock ticks elapsed since the scheduled start
        of the show. If the current time is before the scheduled start, returns
        a negative number.

        Returns zero if there is no scheduled start time yet.

        Parameters:
            now: the number of seconds elapsed since the Unix epoch, according
                to the internal clock of the server.

        Returns:
            the number of clock ticks since the scheduled start of the show,
            assuming 10 ticks per second
        """
        if self._start_time is None:
            return 0.0
        else:
            return (now - self._start_time) * 10

    @property
    def running(self) -> bool:
        return self._start_time is not None

    @property
    def start_time(self) -> Optional[float]:
        """The scheduled start time, in seconds since the UNIX epoch, or ``None``
        if no start time has been scheduled yet.
        """
        return self._start_time

    @start_time.setter
    def start_time(self, value: Optional[float]):
        if self._start_time == value:
            return

        old_value = self._start_time
        running = self.running

        self._start_time = value

        if self.running != running:
            if self.running:
                self.started.send(self)
            else:
                self.stopped.send(self)
        else:
            if old_value is not None and value is not None:
                delta = (old_value - value) * self.ticks_per_second
            else:
                # should not happen
                delta = 0.0  # pragma: no cover
            self.changed.send(self, delta=delta)

    @property
    def ticks_per_second(self) -> int:
        return 10


class ClockSynchronizationHandler:
    """Class that holds references to a primary and a secondary clock and that
    synchronizes the two clocks such that the secondary clock is running if and
    only if the primary clock is running and the offset between the two clocks
    is constant.
    """

    _enabled: bool = False

    _primary_clock: Optional[Clock] = None
    _secondary_clock: Optional[ShowClock] = None

    _subscribed_clock: Optional[Clock] = None
    """The clock whose signals the handler is currently subscribed to. Updated
    dynamically when the enabled / disabled state or the primary clock changes.
    """

    _primary_ticks_for_zero_secondary_ticks: float = 0
    """Tick count displayed on the primary clock that should correspond to zero
    ticks on the secondary clock.
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
    def secondary_clock(self) -> Optional[ShowClock]:
        """The secondary clock; ``None`` means that it has not been assigned
        yet.

        Changing the clock while the synchronization is active will _not_ reset
        the state of the old clock as it is deassigned, but it _will_ update the
        state of the new clock immediately.
        """
        return self._secondary_clock

    @secondary_clock.setter
    def secondary_clock(self, value: Optional[ShowClock]) -> None:
        if self._secondary_clock == value:
            return

        self._secondary_clock = value
        self._update_secondary_clock()

    def disable_and_stop(self) -> None:
        """Disables the synchronization mechanism and stops the secondary
        clock.
        """
        self._primary_clock = None
        self._primary_ticks_for_zero_secondary_ticks = 0.0
        self._subscribe_to_or_unsubscribe_from_primary_clock()
        self._update_secondary_clock()

        # Force-stop the secondary clock because the previous
        # _update_secondary_clock() call had no effect if we were already
        # disabled
        if self._secondary_clock:
            self._secondary_clock.start_time = None

        # Record that we are indeed disabled now
        self._enabled = False

    def synchronize_to(self, clock: Clock, ticks: float) -> None:
        """Enables the synchronization mechanism and attaches it to the given
        primary clock.

        Parameters:
            clock: the primary clock to synchronize to
            ticks: the number of ticks on the primary clock that should belong
                to zero ticks in the secondary clock
        """
        self._enabled = True
        self._primary_clock = clock
        self._primary_ticks_for_zero_secondary_ticks = ticks
        self._subscribe_to_or_unsubscribe_from_primary_clock()
        self._update_secondary_clock()

    def _calculate_desired_state_of_secondary_clock(
        self, now: float
    ) -> Tuple[bool, Optional[float]]:
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
        primary_tick_diff = (
            ticks_on_primary - self._primary_ticks_for_zero_secondary_ticks
        )
        time_diff_sec = primary_tick_diff / self._primary_clock.ticks_per_second
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
            start_time = None
        else:
            start_time = now - time_on_secondary_clock

        self._secondary_clock.start_time = start_time if should_run else None
