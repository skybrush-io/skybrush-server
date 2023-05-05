"""A registry that maps identifiers of local position systems (LPS) to the
instances themselves. Other extensions that provide support for specific types
of local positioning systems need to register these in the LPS registry.
"""

from contextlib import contextmanager
from functools import partial
from typing import Iterator, Optional

from flockwave.server.model import default_id_generator
from flockwave.server.registries.base import RegistryBase
from flockwave.server.registries.objects import ObjectRegistryProxy
from flockwave.server.types import Disposer

from .model import LocalPositioningSystem, LocalPositioningSystemType

__all__ = ("LocalPositioningSystemRegistry", "LocalPositioningSystemTypeRegistry")


class LocalPositioningSystemTypeRegistry(RegistryBase[LocalPositioningSystemType]):
    """A registry that maps identifiers of local positioning system (LPS) types
    to the LPS classes themselves. Other extensions that provide support for a
    specific type of LPS needs to register its handler class in the LPS type
    registry.
    """

    def add(self, id: str, type: LocalPositioningSystemType) -> Disposer:
        """Registers a local positioning system (LPS) type in the registry.

        Parameters:
            id: the identifier of the LPS type
            type: the LPS type to register

        Returns:
            a disposer function that can be called to deregister the LPS type
        """
        if id in self._entries:
            raise KeyError(f"Local positioning system type ID already taken: {id}")
        self._entries[id] = type
        return partial(self._entries.__delitem__, id)

    @contextmanager
    def use(
        self, id: str, type: LocalPositioningSystemType
    ) -> Iterator[LocalPositioningSystemType]:
        """Adds a new local positioning system (LPS) type, hands control back to
        the caller in a context, and then removes the LPS type when the caller
        exits the context.

        Parameters:
            id: the identifier of the LPS type
            type: the LPS type to register

        Yields:
            LocalPositioningSystemType: the LPS type that was added
        """
        disposer = self.add(id, type)
        try:
            yield type
        finally:
            disposer()


class LocalPositioningSystemRegistry(ObjectRegistryProxy[LocalPositioningSystem]):
    """Registry that maps local positioning system (LPS) identifiers to the
    model objects of the local positioning systems themselves.

    This registry is a view into the global object registry of the application
    such that it enumerates only the local positioning systems in the object
    registry.

    It is assumed that LPS instances are registered in the global object registry
    only via this proxy object, never directly.
    """

    _lps_type_registry: LocalPositioningSystemTypeRegistry
    """Registry that associates string identifiers of local positioning system
    (LPS) types to the corresponding LocalPositioningSystemType_ objects.
    """

    def __init__(
        self,
        lps_type_registry: LocalPositioningSystemTypeRegistry,
    ):
        """Constructor.

        Parameters:
            lps_type_registry: registry that associates string identifiers
                of LPS types to the corresponding LocalPositioningSystemType_ objects
        """
        super().__init__()
        self._lps_type_registry = lps_type_registry

    def create(self, type: str, id: Optional[str] = None) -> LocalPositioningSystem:
        """Creates a new local positioning system (LPS) of the given type, adds
        it to the registry and returns the corresponding state object.

        It is the responsibility of the caller to remove the LPS when it is not
        needed any more. To simplify cleanup, consider using the
        `create_and_use()` context manager instead, which will remove the LPS
        when its associated context is exited.

        Parameters:
            type: the identifier of the type of the LPS
            id: a preferred identifier for the local positioning system;
                ``None`` means to generate a random identifier

        Raises:
            KeyError: if the given LPS type is not registered
            RuntimeError: if the object registry was not assigned to the
                LPS registry yet
        """
        if self._object_registry is None:
            raise RuntimeError(
                "object registry has not been assigned to LPS registry yet"
            )

        lps_type = self._lps_type_registry.find_by_id(type)

        if id is None:
            while True:
                lps_id = default_id_generator()
                if lps_id not in self._object_registry:
                    break
        else:
            lps_id = id
            if lps_id in self._object_registry:
                raise ValueError(f"object ID already taken: {id!r}")

        lps: LocalPositioningSystem = lps_type.create()
        lps._id = lps_id
        lps.type = type

        # Fill in the human-readable name of the LPS from the name of the
        # LPS type and the ID if we haven't assigned a name
        if not lps.name:
            lps.name = f"{lps_type.name} {lps_id}"

        return self._add(lps)

    @contextmanager
    def create_and_use(
        self, type: str, id: Optional[str] = None
    ) -> Iterator[LocalPositioningSystem]:
        """Context manager that creates a new local positioning system (LPS)
        instance of the given type, adds it to the registry and yields the
        LPS instance. Automatically removes the LPS instance when the context
        is exited.

        Args:
            id: a preferred identifier for the local positioning system;
                ``None`` means to generate a random identifier

        Yields:
            the created local positioning system instance
        """
        lps = self.create(type, id)
        try:
            yield lps
        finally:
            self.remove_by_id(lps.id)
