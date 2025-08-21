"""A registry that contains information about all the weather providers that
the server knows.
"""

__all__ = ("WeatherProviderRegistry",)

from contextlib import contextmanager
from functools import partial
from typing import Iterable, Iterator, Optional

from flockwave.server.model.weather import WeatherProvider
from flockwave.server.types import Disposer

from .base import RegistryBase


class WeatherProviderRegistry(RegistryBase[WeatherProvider]):
    """Registry that contains information about all the weather providers that
    the server knows.

    The registry allows us to quickly retrieve a weather provider by its
    identifier.
    """

    _priorities: dict[str, int]
    """Priorities of registered weather providers."""

    _ordered_entries: list[tuple[str, WeatherProvider]]
    """Ordered list of registered weather providers by priority."""

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self._priorities = {}
        self._ordered_entries = []

    def add(self, provider: WeatherProvider, *, id: str, priority: int = 0) -> Disposer:
        """Registers a weather provider in the registry.

        Parameters:
            provider: the weather provider to register
            id: the identifier that the provider will be accessible with

        Returns:
            a disposer function that can be called with no arguments to
            unregister the weather provider

        Throws:
            KeyError: if the ID of the provider is already taken by a different
                provider
        """
        old_provider = self._entries.get(id, None)
        if old_provider is not None and old_provider != provider:
            raise KeyError(f"Weather provider ID already taken: {id}")

        self._entries[id] = provider
        self._priorities[id] = priority

        self._ordered_entries.append((id, provider))
        self._ordered_entries.sort(key=lambda x: self._priorities[x[0]], reverse=True)

        return partial(self.remove_by_id, id)

    def iter_providers_by_priority(self) -> Iterable[WeatherProvider]:
        """Iterates over the weather providers in the registry such that
        weather providers with a higher priority are returned earlier.
        """
        return (provider for _, provider in self._ordered_entries)

    def remove_by_id(self, id: str) -> Optional[WeatherProvider]:
        """Removes the weather provider with the given ID from the registry.

        This function is a no-op if no weather provider is registered with the
        given ID.

        Parameters:
            id: the ID of the weather provider to deregister

        Returns:
            the weather provider that was deregistered, or ``None`` if no weather
            provider was registered with the given ID
        """
        self._priorities.pop(id, None)

        provider = self._entries.pop(id, None)
        if provider:
            filtered_entries = [
                entry for entry in self._ordered_entries if entry[0] != id
            ]
            self._ordered_entries = filtered_entries

        return provider

    @contextmanager
    def use(
        self, provider: WeatherProvider, *, id: str, priority: int = 0
    ) -> Iterator[WeatherProvider]:
        """Temporarily adds a new weather provider, hands control back to the
        caller in a context, and then removes the weather provider when the
        caller exits the context.

        Parameters:
            provider: the weather provider to add
            id: the identifier that the provider will be accessible with

        Yields:
            the weather provider that was added
        """
        disposer = self.add(provider, id=id, priority=priority)
        try:
            yield provider
        finally:
            disposer()
