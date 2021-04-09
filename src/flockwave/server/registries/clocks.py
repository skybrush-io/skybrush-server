"""A registry that contains information about all the clocks and timers that
the server knows.
"""

__all__ = ("ClockRegistry",)

from blinker import Signal
from contextlib import contextmanager
from typing import Optional

from ..model.clock import Clock

from .base import RegistryBase


class ClockRegistry(RegistryBase):
    """Registry that contains information about all the clocks and timers
    managed by the server.

    The registry allows us to quickly retrieve information about a clock
    by its identifier, or the status of the clock (i.e. whether it is
    running or not and how it relates to the system time).

    Attributes:
        clock_changed (Signal): signal that is dispatched when one of the
            clocks registered in the clock registry is changed (adjusted),
            started or stopped. You need to subscribe to the signals of the
            clock on your own if you are interested in the exact signal that
            caused a ``clock_changed`` signal to be dispatched from the
            registry.
    """

    clock_changed = Signal()

    def add(self, clock: Clock) -> None:
        """Registers a clock in the registry.

        This function is a no-op if the clock is already registered.

        Parameters:
            clock: the clock to register

        Throws:
            KeyError: if the ID of the clock is already taken by a different clock
        """
        old_clock = self._entries.get(clock.id, None)
        if old_clock is not None and old_clock != clock:
            raise KeyError(f"Clock ID already taken: {clock.id}")
        self._entries[clock.id] = clock
        self._subscribe_to_clock(clock)

    def remove(self, clock: Clock) -> Optional[Clock]:
        """Removes the given clock from the registry.

        This function is a no-op if the clock is not registered.

        Parameters:
            clock: the clock to deregister

        Returns:
            the clock that was deregistered, or ``None`` if the clock was not
                registered
        """
        return self.remove_by_id(clock.id)

    def remove_by_id(self, clock_id: str) -> Optional[Clock]:
        """Removes the clock with the given ID from the registry.

        This function is a no-op if no clock is registered with the given ID.

        Parameters:
            clock_id: the ID of the clock to deregister

        Returns:
            the clock that was deregistered, or ``None`` if the clock was not
            registered
        """
        clock = self._entries.pop(clock_id, None)
        if clock:
            self._unsubscribe_from_clock(clock)
        return clock

    @contextmanager
    def use(self, clock: Clock):
        """Temporarily adds a new clock, hands control back to the caller in a
        context, and then removes the clock when the caller exits the context.

        Parameters:
            clock (Clock): the clock to add

        Yields:
            Clock: the clock object that was added
        """
        self.add(clock)
        try:
            yield clock
        finally:
            self.remove(clock)

    def _subscribe_to_clock(self, clock: Clock) -> None:
        """Subscribes to the signals of the given clock in order to
        redispatch them.
        """
        clock.changed.connect(self._send_clock_changed_signal, sender=clock)
        clock.started.connect(self._send_clock_changed_signal, sender=clock)
        clock.stopped.connect(self._send_clock_changed_signal, sender=clock)

    def _unsubscribe_from_clock(self, clock: Clock) -> None:
        """Unsubscribes from the signals of the given clock."""
        clock.changed.disconnect(self._send_clock_changed_signal, sender=clock)
        clock.started.disconnect(self._send_clock_changed_signal, sender=clock)
        clock.stopped.disconnect(self._send_clock_changed_signal, sender=clock)

    def _send_clock_changed_signal(self, sender, **kwds):
        """Sends a ``clock_changed`` signal in response to an actual
        ``started``, ``stopped`` or ``changed`` signal from one of the clocks
        in the registry. The ``clock`` argument of the signal being sent will
        refer to the clock that sent the original signal.
        """
        self.clock_changed.send(self, clock=sender)
