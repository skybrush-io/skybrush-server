"""A registry that contains the authentication methods that are supported by
the server.
"""

__all__ = ("AuthenticationMethodRegistry",)

from contextlib import contextmanager
from typing import Optional

from .base import RegistryBase
from ..model.authentication import AuthenticationMethod


class AuthenticationMethodRegistry(RegistryBase):
    """Registry that contains the authentication methods that are supported
    by the server.

    The registry allows us to quickly retrieve the authentication method handler
    by its identifier.
    """

    def add(self, method: AuthenticationMethod):
        """Registers an authentication method in the registry.

        Parameters:
            method: the authentication method to register

        Throws:
            KeyError: if the ID of the method is already taken by another method
        """
        old_method = self._entries.get(method.id, None)
        if old_method is not None and old_method != method:
            raise KeyError(f"Authentication method ID already taken: {method.id}")
        self._entries[method.id] = method

    def remove(self, method: AuthenticationMethod) -> Optional[AuthenticationMethod]:
        """Removes the given authentication method from the registry.

        This function is a no-op if the method is not registered.

        Parameters:
            method: the authentication method to deregister

        Returns:
            the method that was deregistered, or ``None`` if the method was not
            registered
        """
        return self.remove_by_id(method.id)

    def remove_by_id(self, method_id: str) -> Optional[AuthenticationMethod]:
        """Removes the authentication method with the given ID from the
        registry.

        This function is a no-op if the method is not registered.

        Parameters:
            method_id (str): the ID of the clock to deregister

        Returns:
            the method that was deregistered, or ``None`` if no method was
            registered with the given ID
        """
        return self._entries.pop(method_id, None)

    @contextmanager
    def use(self, method):
        """Temporarily adds a new authentication method to the registry, hands
        control back to the caller in a context, and then removes the method
        when the caller exits the context.

        Arguments:
            method: the authentication method to register
        """
        self.add(method)
        try:
            yield
        finally:
            self.remove(method)
