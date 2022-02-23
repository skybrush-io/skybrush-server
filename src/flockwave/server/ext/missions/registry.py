"""A registry that maps identifiers of mission planners to the instances
themselves. Other extensions that provide mission planning services need to
register themselves in the mission planner registry so their services can
be used by clients.
"""

from contextlib import contextmanager
from functools import partial
from typing import Iterator

from flockwave.server.registries.base import RegistryBase
from flockwave.server.types import Disposer

from .types import MissionPlanner

__all__ = ("MissionPlannerRegistry",)


class MissionPlannerRegistry(RegistryBase[MissionPlanner]):
    """A registry that maps identifiers of mission planners to the instances
    themselves. Other extensions that provide mission planning services need to
    register themselves in the mission planner registry so their services can
    be used by clients.
    """

    def add(self, id: str, planner: MissionPlanner) -> Disposer:
        """Registers a mission planner in the registry.

        Parameters:
            id: the identifier of the mission planner
            planner: the mission planner to add

        Returns:
            a disposer function that can be called to deregister the planner
        """
        if id in self._entries:
            raise KeyError(f"Mission planner ID already taken: {id}")
        self._entries[id] = planner
        return partial(self._entries.__delitem__, id)

    @contextmanager
    def use(self, id: str, planner: MissionPlanner) -> Iterator[MissionPlanner]:
        """Adds a new mission planner, hands control back to the caller in a
        context, and then removes the mission planner when the caller exits the
        context.

        Parameters:
            id: the identifier of the mission planner
            planner: the mission planner to add

        Yields:
            MissionPlanner: the preset object that was added
        """
        disposer = self.add(id, planner)
        try:
            yield planner
        finally:
            disposer()
