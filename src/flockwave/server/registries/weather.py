"""A registry that contains information about all the weather providers that
the server knows.
"""

__all__ = ("WeatherProviderRegistry",)

from contextlib import contextmanager
from functools import partial
from typing import Iterator, Optional

from flockwave.server.model.weather import WeatherProvider
from flockwave.server.types import Disposer

from .base import RegistryBase


class WeatherProviderRegistry(RegistryBase[WeatherProvider]):
    """Registry that contains information about all the weather providers that
    the server knows.

    The registry allows us to quickly retrieve a weather provider by its
    identifier.
    """

    def add(self, provider: WeatherProvider, *, id: str) -> Disposer:
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
        return partial(self.remove_by_id, id)

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
        return self._entries.pop(id, None)

    @contextmanager
    def use(self, provider: WeatherProvider, *, id: str) -> Iterator[WeatherProvider]:
        """Temporarily adds a new weather provider, hands control back to the
        caller in a context, and then removes the weather provider when the
        caller exits the context.

        Parameters:
            provider: the weather provider to add
            id: the identifier that the provider will be accessible with

        Yields:
            the weather provider that was added
        """
        disposer = self.add(provider, id=id)
        try:
            yield provider
        finally:
            disposer()
