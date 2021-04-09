"""A registry that contains information about UPnP services that the server
provides, their IDs and URLs.
"""

__all__ = ("UPnPServiceRegistry",)

from contextlib import contextmanager
from typing import Callable, Optional, Union

from flockwave.server.registries.base import RegistryBase


URIOrCallableReturningURI = Union[str, Callable[[str], Optional[str]]]


class UPnPServiceRegistry(RegistryBase):
    """Registry that contains information about the UPnP services that the
    server provides.
    """

    def add(self, service_id: str, uri: URIOrCallableReturningURI) -> None:
        """Registers a UPnP service with the given URL in the registry.

        Parameters:
            service_id: ID of the service to register
            uri: the URI of the service, or a callable that returns the URI
                of the service when called with an IP address as the first
                argument. In such cases, the callable should attempt to return
                a service that is in the same subnet as the given IP address.
                The callable may also return `None` to indicate that the service
                is temporarily unavailable.

        Throws:
            KeyError: if the service ID of the clock is already taken by a
                different service
        """
        if service_id in self._entries:
            raise KeyError(f"Service ID already taken: {service_id}")

        self._entries[service_id] = uri

    def remove(self, service_id: str) -> Optional[URIOrCallableReturningURI]:
        """Removes the service with the given ID from the registry.

        This function is a no-op if the service is not registered.

        Parameters:
            service_id: ID of the service to deregister

        Returns:
            the URL of the service that was deregistered, or ``None`` if the
            service was not registered
        """
        return self._entries.pop(service_id, None)

    @contextmanager
    def use(self, service_id: str, uri: URIOrCallableReturningURI):
        """Temporarily adds a new service URL with a given service ID, hands
        control back to the caller in a context, and then removes the service
        when the caller exits the context.

        Parameters:
            service_id: ID of the service to register
            uri: the URI of the service; see `add()` for more information
        """
        self.add(service_id, uri)
        try:
            yield
        finally:
            self.remove(service_id)
