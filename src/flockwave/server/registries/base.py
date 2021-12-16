"""Abstract base class for registries that keep track of "things" by
string identifiers.
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod, abstractproperty
from typing import (
    Callable,
    Dict,
    Generic,
    Iterable,
    Optional,
    TypeVar,
    TYPE_CHECKING,
    Union,
)

if TYPE_CHECKING:
    from flockwave.server.model.messages import FlockwaveNotification, FlockwaveResponse


__all__ = ("Registry", "RegistryBase")


T = TypeVar("T")


class Registry(Generic[T], metaclass=ABCMeta):
    """Interface specification for registries that keep track of "things"
    by string identifiers.
    """

    @abstractmethod
    def contains(self, entry_id: str) -> bool:
        """Returns whether the given entry ID is already used in this
        registry.

        Parameters:
            entry_id: the entry ID to check

        Returns:
            whether the given entry ID is already used
        """
        raise NotImplementedError

    @abstractmethod
    def find_by_id(self, entry_id: str) -> T:
        """Returns an entry from this registry given its ID.

        Parameters:
            entry_id: the ID of the entry to retrieve

        Returns:
            the entry with the given ID

        Raises:
            KeyError: if the given ID does not refer to an entry in the
                registry
        """
        raise NotImplementedError

    @abstractproperty
    def ids(self) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers
        that are known to the registry.
        """
        raise NotImplementedError

    def ids_matching(self, predicate: Callable[[T], bool]) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers in the
        registry where the associated object matches the given predicate.

        Parameters:
            predicate: the predicate to call for each object in the registry
        """
        raise NotImplementedError

    @abstractproperty
    def num_entries(self) -> int:
        """Returns the number of entries in the registry."""
        raise NotImplementedError

    def __contains__(self, entry_id: str) -> bool:
        return self.contains(entry_id)

    def __getitem__(self, entry_id: str) -> T:
        return self.find_by_id(entry_id)

    def __len__(self) -> int:
        return self.num_entries


class RegistryBase(Generic[T], Registry[T]):
    """Abstract base class for registries that keep track of "things" by
    string identifiers.
    """

    _entries: Dict[str, T]

    def __init__(self):
        """Constructor."""
        super().__init__()
        self._entries = {}

    def contains(self, entry_id: str) -> bool:
        """Returns whether the given entry ID is already used in this
        registry.

        Parameters:
            entry_id: the entry ID to check

        Returns:
            whether the given entry ID is already used
        """
        return entry_id in self._entries

    def find_by_id(self, entry_id: str) -> T:
        """Returns an entry from this registry given its ID.

        Parameters:
            entry_id (str): the ID of the entry to retrieve

        Returns:
            object: the entry with the given ID

        Raises:
            KeyError: if the given ID does not refer to an entry in the
                registry
        """
        return self._entries[entry_id]

    @property
    def ids(self) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers
        that are known to the registry.
        """
        return sorted(self._entries.keys())

    def ids_matching(self, predicate: Callable[[T], bool]) -> Iterable[str]:
        """Returns an iterable that iterates over all the identifiers in the
        registry where the associated object matches the given predicate.

        Parameters:
            predicate: the predicate to call for each object in the registry
        """
        return (key for key, value in self._entries.items() if predicate(value))

    @property
    def num_entries(self):
        """Returns the number of entries in the registry."""
        return len(self._entries)

    def __iter__(self):
        """Iterates over the entries in the registry in the same order as the
        IDs returned by the `ids` property.
        """
        for id in self.ids:
            yield self._entries[id]


def find_in_registry(
    registry: Optional[Registry[T]],
    entry_id: str,
    *,
    predicate: Optional[Callable[[T], bool]] = None,
    response: Optional[Union["FlockwaveNotification", "FlockwaveResponse"]] = None,
    failure_reason: Optional[str] = None,
) -> Optional[T]:
    """Finds an entry in the given registry with the given ID or
    registers a failure in the given response object if there is no
    such entry in the registry.

    Parameters:
        entry_id: the ID of the entry to find
        registry: the registry in which to find the entry; `None` is accepted
            and it is assumed to be an empty registry
        predicate: optional predicate to call for the entry if it was found;
            if the predicate returns `False`, we pretend that the entry does not
            exist.
        response: the response in which the failure can be registered
        failure_reason: the failure reason to register

    Returns:
        the entry from the registry with the given ID or ``None`` if there is
        no such entry (or if the registry itself was ``None``)
    """
    entry = None
    exists = False

    if registry:
        try:
            entry = registry.find_by_id(entry_id)
            exists = True
        except KeyError:
            exists = False

        exists = exists and (not predicate or predicate(entry))

    if not exists:
        if hasattr(response, "add_error"):
            response.add_error(entry_id, failure_reason)  # type: ignore
        return None
    else:
        return entry
