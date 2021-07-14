"""A registry that contains information about all the model objects that the
server knows.
"""

from blinker import Signal
from contextlib import contextmanager
from math import inf
from typing import Callable, Iterable, Iterator, Optional, Type, Union

from flockwave.server.model import ModelObject

from .base import RegistryBase
from .errors import RegistryFull

__all__ = ("ObjectRegistry",)


class ObjectRegistry(RegistryBase[ModelObject]):
    """Registry that contains information about all the objects seen or tracked
    by the server.

    Attributes:
        added (Signal): signal that is sent by the registry when a new object
            has been added to the registry. The signal has a keyword
            argment named ``object`` that contains the object that has just been
            added to the registry.

        removed (Signal): signal that is sent by the registry when an object
            has been removed from the registry. The signal has a keyword
            argument named ``object`` that contains the object that has just been
            removed from the registry.
    """

    added = Signal(
        doc="""\
        Signal sent whenever a new object is added to the registry.

        Parameters:
            object (ModelObject): the object that was added
        """
    )
    removed = Signal(
        doc="""\
        Signal sent whenever an object was removed from the registry.

        Parameters:
            object (ModelObject): the object that was removed
        """
    )

    def __init__(self) -> None:
        self._size_limit = inf
        super().__init__()

    def add(self, object: ModelObject) -> None:
        """Registers an object in the registry.

        This function is a no-op if the object is already registered.

        Parameters:
            object: the object to register

        Throws:
            KeyError: if the ID is already registered for a different object
            RegistryFull: if the registry is full and no additional objects of
                the given type can be registered
        """
        old_object = self._entries.get(object.id, None)
        if old_object is not None and old_object != object:
            raise KeyError("Object ID already taken: {0!r}".format(object.id))

        self._ensure_has_free_slot_for_object(object)
        self._entries[object.id] = object
        self.added.send(self, object=object)

    def add_if_missing(
        self, id: str, factory: Callable[[str], ModelObject]
    ) -> ModelObject:
        """Checks whether an object with the given ID already exists in the
        registry, and if not, creates it with a factory function and then adds
        it to the registry.

        Parameters:
            id: the ID of the object to look for
            factory: a callable that can be called with a single object ID to
                create the object to be registered if it is missing

        Returns:
            the object in the registry with the given ID; freshly created if it
            was not in the registry
        """
        if not self.contains(id):
            self.add(factory(id))
        return self.find_by_id(id)

    def ids_by_type(self, cls: Union[str, Type[ModelObject]]) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers in the
        registry where the associated object is an instance of the given type.

        Parameters:
            cls: the model object class to match for each object in the registry,
                or its registered string identifier in the ModelObject_ base
                class
        """
        resolved_cls = ModelObject.resolve_type(cls) if isinstance(cls, str) else cls
        if resolved_cls is None:
            return []
        else:
            return (
                key
                for key, value in self._entries.items()
                if isinstance(value, resolved_cls)
            )

    def ids_by_types(
        self, classes: Iterable[Union[Type[ModelObject], str]]
    ) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers in the
        registry where the associated object matches the given predicate.

        Parameters:
            cls: the model object class to match for each object in the registry,
                or its registered string identifier in the ModelObject_ base
                class
        """
        filter = []
        for cls in classes:
            cls = ModelObject.resolve_type(cls) if isinstance(cls, str) else cls
            if cls is not None:
                filter.append(cls)

        if not filter:
            return []
        else:
            filter = tuple(filter)
            return (
                key for key, value in self._entries.items() if isinstance(value, filter)
            )

    def remove(self, object: ModelObject) -> Optional[ModelObject]:
        """Removes the given object from the registry.

        This function is a no-op if the object is not registered.

        Parameters:
            object: the object to deregister

        Returns:
            the object that was deregistered, or ``None`` if the object was not
            registered
        """
        return self.remove_by_id(object.id)

    def remove_by_id(self, object_id: str) -> Optional[ModelObject]:
        """Removes the object with the given ID from the registry.

        This function is a no-op if the object is not registered.

        Parameters:
            object_id: the ID of the object to deregister

        Returns:
            the object that was deregistered, or ``None`` if the object was not
            registered
        """
        object = self._entries.pop(object_id, None)
        if object is not None:
            self.removed.send(self, object=object)
        return object

    @property
    def size_limit(self) -> float:
        return self._size_limit

    @size_limit.setter
    def size_limit(self, value: float) -> None:
        self._size_limit = max(value, 0)

    @contextmanager
    def use(self, *args: ModelObject) -> Iterator[None]:
        """Temporarily adds one or more new objects to the registry, hands
        control back to the caller in a context, and then removes the objects
        when the caller exits the context.

        Arguments:
            args: the objects to add
        """
        added = []
        try:
            for object in args:
                self.add(object)
                added.append(object)
            yield
        finally:
            for object in added:
                self.remove(object)

    def _ensure_has_free_slot_for_object(self, object: ModelObject) -> None:
        """Ensures that there is at least one free slot in the object registry
        to store the given model object.

        Technically speaking, an object registry should be able to hold an
        arbitrary amount of objects. However, we sometimes enforce limits on the
        number of objects that can be held in the registry due to restrictions
        from the license manager. These limits are enforced here.

        Raises:
            RegistryFull: if the registry is full and no additional objects of
                the given type can be registered
        """
        if len(self._entries) >= self._size_limit:
            raise RegistryFull
