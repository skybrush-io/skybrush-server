"""A registry that contains information about all the clocks and timers that
the server knows.
"""

__all__ = ("ClockRegistry", )

from .base import RegistryBase


class ClockRegistry(RegistryBase):
    """Registry that contains information about all the clocks and timers
    managed by the server.

    The registry allows us to quickly retrieve information about a clock
    by its identifier, or the status of the clock (i.e. whether it is
    running or not and how it relates to the system time).
    """

    def add(self, clock):
        """Registers a clock with the given identifier in the registry.

        This function is a no-op if the clock is already registered.

        Parameters:
            clock (Clock): the clock to register

        Throws:
            KeyError: if the ID is already registered for a different clock
        """
        old_clock = self._entries.get(clock.id, None)
        if old_clock is not None and old_clock != clock:
            raise KeyError("Clock ID already taken: {0!r}".format(clock.id))
        self._entries[clock.id] = clock

    def remove(self, clock):
        """Removes the given clock from the registry.

        This function is a no-op if the clock is not registered.

        Parameters:
            clock (Clock): the clock to deregister

        Returns:
            Clock or None: the clock that was deregistered, or ``None`` if
                the clock was not registered
        """
        return self.remove_by_id(clock.id)

    def remove_by_id(self, clock_id):
        """Removes the clock with the given ID from the registry.

        This function is a no-op if the clock is not registered.

        Parameters:
            clock_id (str): the ID of the clock to deregister

        Returns:
            Clock or None: the clock that was deregistered, or ``None`` if
                the clock was not registered
        """
        return self._entries.pop(clock_id)
