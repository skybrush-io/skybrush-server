"""Model classes related to a single object in the object registry of the
server.
"""

from abc import ABCMeta, abstractproperty
from typing import Any, Optional

from flockwave.server.logger import log as base_log

log = base_log.getChild("object")


_type_registry = {}


class ModelObject(metaclass=ABCMeta):
    """Abstract object that defines the interface of generic objects tracked
    by the Flockwave server.
    """

    @classmethod
    def register(cls, type: str) -> None:
        """Method that should be called by subclasses if they wish to register
        themselves in the Flockwave messaging system with a given type name.

        For instance, UAV subclasses register themselves as `uav` in the
        messaging system such that calling `OBJ-LIST` filtered to `uav` will
        return all registered objects in the object registry that are subclasses
        of UAV.
        """
        if type in _type_registry:
            raise ValueError(f"{repr(type)} is already registered as a type")
        _type_registry[type] = cls

    @staticmethod
    def resolve_type(type: str) -> Optional[Any]:
        """Resolves the given model object type specified as a string (as it
        appears in the Flockwave protocol) into the corresponding model object
        class, or `None` if the given type does not map to a model object class.
        """
        return _type_registry.get(type)

    @abstractproperty
    def id(self):
        """A unique identifier for the object, assigned at construction time."""
        raise NotImplementedError
