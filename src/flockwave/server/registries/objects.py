"""A registry that contains information about all the model objects that the
server knows.
"""

__all__ = ("ObjectRegistry",)

from blinker import Signal
from contextlib import contextmanager
from typing import Iterable, Optional, Type, Union

from .base import RegistryBase

from ..model import ModelObject


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

    def add(self, object: ModelObject) -> None:
        """Registers an object in the registry.

        This function is a no-op if the object is already registered.

        Parameters:
            object: the object to register

        Throws:
            KeyError: if the ID is already registered for a different object
        """
        old_object = self._entries.get(object.id, None)
        if old_object is not None and old_object != object:
            raise KeyError("Object ID already taken: {0!r}".format(object.id))
        self._entries[object.id] = object
        self.added.send(self, object=object)

    def ids_by_type(self, cls: Union[str, Type[ModelObject]]) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers in the
        registry where the associated object is an instance of the given type.

        Parameters:
            cls: the model object class to match for each object in the registry,
                or its registered string identifier in the ModelObject_ base
                class
        """
        cls = ModelObject.resolve_type(cls) if isinstance(cls, str) else cls
        if cls is None:
            return []
        else:
            return (
                key for key, value in self._entries.items() if isinstance(value, cls)
            )

    def ids_by_types(self, classes: Iterable[Union[Type[ModelObject], str]]):
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
            return (
                key
                for key, value in self._entries.items()
                if isinstance(value, tuple(filter))
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

    @contextmanager
    def use(self, *args: ModelObject) -> None:
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
