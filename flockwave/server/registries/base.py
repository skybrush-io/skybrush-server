"""Abstract base class for registries that keep track of "things" by
string identifiers.
"""

from abc import ABCMeta, abstractmethod, abstractproperty
from future.utils import with_metaclass

__all__ = ("Registry", "RegistryBase")


class Registry(with_metaclass(ABCMeta, object)):
    """Interface specification for registries that keep track of "things"
    by string identifiers.
    """

    @abstractmethod
    def contains(self, entry_id):
        """Returns whether the given entry ID is already used in this
        registry.

        Parameters:
            entry_id (str): the entry ID to check

        Returns:
            bool: whether the given entry ID is already used
        """
        raise NotImplementedError

    @abstractmethod
    def find_by_id(self, entry_id):
        """Returns an entry from this registry given its ID.

        Parameters:
            entry_id (str): the ID of the entry to retrieve

        Returns:
            object: the entry with the given ID

        Raises:
            KeyError: if the given ID does not refer to an entry in the
                registry
        """
        raise NotImplementedError

    @abstractproperty
    def ids(self):
        """Returns an iterable that iterates over all the identifiers
        that are known to the registry.
        """
        raise NotImplementedError

    @abstractproperty
    def num_entries(self):
        """Returns the number of entries in the registry."""
        raise NotImplementedError

    def __contains__(self, entry_id):
        return self.contains(entry_id)

    def __getitem__(self, entry_id):
        return self.find_by_id(entry_id)

    def __len__(self):
        return self.num_entries


class RegistryBase(Registry):
    """Abstract base class for registries that keep track of "things" by
    string identifiers.
    """

    def __init__(self):
        """Constructor."""
        super(RegistryBase, self).__init__()
        self._entries = {}

    def contains(self, entry_id):
        """Returns whether the given entry ID is already used in this
        registry.

        Parameters:
            entry_id (str): the entry ID to check

        Returns:
            bool: whether the given entry ID is already used
        """
        return entry_id in self._entries

    def find_by_id(self, entry_id):
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
    def ids(self):
        """Returns an iterable that iterates over all the identifiers
        that are known to the registry.
        """
        return sorted(self._entries.keys())

    @property
    def num_entries(self):
        """Returns the number of entries in the registry."""
        return len(self._entries)
