"""A registry that holds all the registered UAV drivers that the server
knows about.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from ..logger import log as base_log
from ..model.uav import UAVDriver

from .base import RegistryBase

__all__ = ("UAVDriverRegistry",)

log = base_log.getChild("registries.uav_drivers")


class UAVDriverRegistry(RegistryBase[UAVDriver]):
    """Registry that holds all the registered UAV drivers that the server
    knows about.
    """

    def add(self, driver: UAVDriver) -> str:
        """Registers a UAV driver instance in the registry and makes up an
        ID for it.

        Parameters:
            driver: the driver to register

        Returns:
            a new, unique ID for the driver
        """
        driver_id = str(id(driver))
        if driver_id in self._entries:
            raise KeyError(f"UAV driver ID already taken: {driver_id}")

        self._entries[driver_id] = driver
        driver_type = type(driver).__name__

        log.debug(
            f"{driver_type} instance registered as a UAV driver",
            extra={"id": driver_id},
        )

        return driver_id

    def remove(self, driver: UAVDriver) -> Optional[UAVDriver]:
        """Removes the given driver from the registry.

        This function is a no-op if the driver is not registered.

        Returns:
            the driver itself if it was deregistered, or ``None`` if the driver
            was not registered
        """
        for id, maybe_driver in self._entries.items():
            if maybe_driver is driver:
                return self.remove_by_id(id)

    def remove_by_id(self, id: str) -> Optional[UAVDriver]:
        """Removes the driver with the given ID from the registry.

        This function is a no-op if no driver is registered with the given ID.

        Parameters:
            id: the ID of the driver to deregister

        Returns:
            the driver that was deregistered, or ``None`` if no driver was
            registered with the given ID
        """
        driver = self._entries.pop(id, None)
        if driver:
            driver_type = type(driver).__name__
            log.debug(
                f"{driver_type} instance deregistered",
                extra={"id": id},
            )
        return driver

    @contextmanager
    def use(self, driver: UAVDriver) -> Iterator[UAVDriver]:
        """Temporarily associates a driver to an ID, hands control back to the
        caller in a context, and then removes the driver when the caller exits
        the context.

        Parameters:
            id: the ID of the driver
            driver: the driver to register

        Yields:
            the driver that was added
        """
        driver_id = self.add(driver)
        try:
            yield driver
        finally:
            self.remove_by_id(driver_id)
