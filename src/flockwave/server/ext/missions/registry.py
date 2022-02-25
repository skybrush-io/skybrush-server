"""A registry that maps identifiers of mission planners to the instances
themselves. Other extensions that provide mission planning services need to
register themselves in the mission planner registry so their services can
be used by clients.
"""

from contextlib import contextmanager
from functools import partial
from typing import Iterator, Optional

from flockwave.server.model import default_id_generator
from flockwave.server.registries.base import RegistryBase
from flockwave.server.types import Disposer

from .types import MissionState, MissionType

__all__ = ("MissionRegistry", "MissionTypeRegistry")


class MissionTypeRegistry(RegistryBase[MissionType]):
    """A registry that maps identifiers of mission types to the mission classes
    themselves. Other extensions that provide missions or mission planning
    services need to register themselves in the mission type registry so their
    services can be used by clients.
    """

    def add(self, id: str, type: MissionType) -> Disposer:
        """Registers a mission type in the registry.

        Parameters:
            id: the identifier of the mission type
            type: the mission type to register

        Returns:
            a disposer function that can be called to deregister the mission type
        """
        if id in self._entries:
            raise KeyError(f"Mission planner ID already taken: {id}")
        self._entries[id] = type
        return partial(self._entries.__delitem__, id)

    @contextmanager
    def use(self, id: str, type: MissionType) -> Iterator[MissionType]:
        """Adds a new mission type, hands control back to the caller in a
        context, and then removes the mission type when the caller exits the
        context.

        Parameters:
            id: the identifier of the mission type
            type: the mission type to register

        Yields:
            MissionType: the mission type that was added
        """
        disposer = self.add(id, type)
        try:
            yield type
        finally:
            disposer()


class MissionRegistry(RegistryBase[MissionState]):
    """Registry that maps mission identifiers to the state objects of the
    missions themselves.
    """

    def create(self, type: str) -> MissionState:
        """Creates a new mission, adds it to the registry and returns the
        corresponding state object.

        Parameters:
            type: the identifier of the type of the mission
        """
        while True:
            mission_id = default_id_generator()
            if mission_id not in self._entries:
                mission = self._entries[mission_id] = MissionState(
                    id=mission_id, type=type
                )
                return mission

    def remove_by_id(self, mission_id: str) -> Optional[MissionState]:
        """Removes the mission with the given ID from the registry.

        This function is a no-op if no mission is registered with the given ID.

        Parameters:
            mission_id: the ID of the mission to deregister

        Returns:
            the mission that was deregistered, or ``None`` if the mission was not
            registered
        """
        return self._entries.pop(mission_id, None)
