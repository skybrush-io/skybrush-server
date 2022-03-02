"""A registry that maps identifiers of mission planners to the instances
themselves. Other extensions that provide mission planning services need to
register themselves in the mission planner registry so their services can
be used by clients.
"""

from blinker import Signal
from contextlib import contextmanager
from functools import partial
from typing import ClassVar, Iterator, Optional

from flockwave.server.model import default_id_generator
from flockwave.server.registries.base import RegistryBase
from flockwave.server.types import Disposer

from .model import Mission, MissionType

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


class MissionRegistry(RegistryBase[Mission]):
    """Registry that maps mission identifiers to the state objects of the
    missions themselves.
    """

    _mission_type_registry: MissionTypeRegistry
    """Registry that associates string identifiers of mission types to the
    corresponding MissionType_ objects.
    """

    mission_added: ClassVar[Signal] = Signal(
        doc="""Signal that is emitted when a new mission is added to the registry."""
    )

    mission_removed: ClassVar[Signal] = Signal(
        doc="""Signal that is emitted when a mission is removed from the registry."""
    )

    def __init__(self, mission_type_registry: MissionTypeRegistry):
        """Constructor.

        Parameters:
            mission_type_registry: registry that associates string identifiers
                of mission types to the corresponding MissionType_ objects
        """
        super().__init__()
        self._mission_type_registry = mission_type_registry

    def create(self, type: str) -> Mission:
        """Creates a new mission, adds it to the registry and returns the
        corresponding state object.

        Parameters:
            type: the identifier of the type of the mission

        Raises:
            KeyError: if the given mission type is not registered
        """
        mission_type = self._mission_type_registry.find_by_id(type)

        while True:
            mission_id = default_id_generator()
            if mission_id not in self._entries:
                mission: Mission = mission_type.create_mission()
                mission.id = mission_id
                mission.type = type
                self._entries[mission_id] = mission
                self.mission_added.send(self, mission=mission)
                return mission

    def remove_by_id(self, mission_id: str) -> Optional[Mission]:
        """Removes the mission with the given ID from the registry.

        This function is a no-op if no mission is registered with the given ID.

        Parameters:
            mission_id: the ID of the mission to deregister

        Returns:
            the mission that was deregistered, or ``None`` if the mission was not
            registered
        """
        mission = self._entries.pop(mission_id, None)
        if mission:
            self.mission_removed.send(self, mission=mission)
