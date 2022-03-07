"""Model classes related to a single object in the object registry of the
server.
"""

from __future__ import annotations

from abc import ABCMeta, abstractproperty
from contextlib import contextmanager
from typing import (
    Callable,
    Dict,
    Iterator,
    Optional,
    Type,
    TypeVar,
    TYPE_CHECKING,
    overload,
)

from flockwave.server.logger import log as base_log

log = base_log.getChild("object")

__all__ = ("ModelObject", "register", "registered", "unregister")

_type_registry: Dict[str, Type["ModelObject"]] = {}

if TYPE_CHECKING:
    from .devices import ObjectNode


T = TypeVar("T", bound="ModelObject")


class ModelObject(metaclass=ABCMeta):
    """Abstract object that defines the interface of generic objects tracked
    by the Skybrush server.
    """

    @staticmethod
    def resolve_type(type: str) -> Optional[Type["ModelObject"]]:
        """Resolves the given model object type specified as a string (as it
        appears in the Flockwave protocol) into the corresponding model object
        class, or `None` if the given type does not map to a model object class.
        """
        return _type_registry.get(type)

    @abstractproperty
    def device_tree_node(self) -> "Optional[ObjectNode]":
        """Returns the ObjectNode_ that represents the root of the part of the
        device tree that corresponds to the model object, or ``None`` if the
        model object does not have to be registered in the device tree.
        """
        raise NotImplementedError

    @abstractproperty
    def id(self):
        """A unique identifier for the object, assigned at construction time."""
        raise NotImplementedError


@overload
def register(
    type: str,
) -> Callable[[Type[T]], Type[T]]:
    ...


@overload
def register(type: str, cls: Type[T]) -> None:
    ...


def register(
    type: str, cls: Optional[Type[T]] = None
) -> Optional[Callable[[Type[T]], Type[T]]]:
    """Registers a ModelObject_ subclass or factory in the Flockwave messaging
    system with a given type name.

    For instance, UAV subclasses can register themselves as `uav` in the
    messaging system such that calling `OBJ-LIST` filtered to `uav` will
    return all registered objects in the object registry that are subclasses
    of UAV.

    Parameters:
        type: the type name to use for the subclass
        cls: the ModelObject_ subclass or factory to register. When omitted,
            returns a decorator that can be applied to a ModelObject_ subclass
    """
    if cls is None:

        def decorator(x):
            register(type, x)
            return x

        return decorator

    else:
        if type in _type_registry:
            raise ValueError(f"{repr(type)} is already registered as a type")
        _type_registry[type] = cls

        return None


def unregister(type: str) -> None:
    """Unregisters a ModelObject_ subclass or factory with the given type name
    from the Flockwave messaging system.

    Parameters:
        type: the type name to unregister
    """
    if type not in _type_registry:
        raise ValueError(f"{repr(type)} is not registered as a type")
    del _type_registry[type]


@contextmanager
def registered(type: str, cls: Type[ModelObject]) -> Iterator[None]:
    """Context manager that temporarily registers the class in the Flockwave
    messaging system with a given type name, and unregisters the class
    when exiting the context.

    Parameters:
        type: the type name to use for the subclass
        cls: the ModelObject_ subclass or factory to register
    """
    register(type, cls)
    try:
        yield
    finally:
        unregister(type)
