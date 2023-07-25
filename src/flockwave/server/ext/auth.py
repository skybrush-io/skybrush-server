"""Base extension that handles authentication-related messages and
keeps track of registered authentication methods.
"""

from contextlib import contextmanager

from flockwave.server.model.authentication import (
    AuthenticationMethod,
    AuthenticationResult,
)
from flockwave.server.model.client import Client
from flockwave.server.registries.base import RegistryBase

from .base import Extension

from typing import Any, Iterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


class AuthenticationMethodRegistry(RegistryBase[AuthenticationMethod]):
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
    def use(self, method: AuthenticationMethod) -> Iterator[AuthenticationMethod]:
        """Temporarily adds a new authentication method to the registry, hands
        control back to the caller in a context, and then removes the method
        when the caller exits the context.

        Arguments:
            method: the authentication method to register

        Yields:
            the authentication method that was registered
        """
        self.add(method)
        try:
            yield method
        finally:
            self.remove(method)


class AuthenticationExtension(Extension):
    """Extension that implements basic handling of authentication-related
    messages in the server.

    Note that this extension does not implement any particular authentication
    method; see other extensions starting with ``auth_`` for that.
    """

    _registry: AuthenticationMethodRegistry
    _required: bool = False

    def __init__(self):
        super().__init__()

        self._registry = AuthenticationMethodRegistry()

    def configure(self, configuration: dict[str, Any]) -> None:
        self._required = bool(configuration.get("required"))

    def exports(self) -> dict[str, Any]:
        return {
            "get_supported_methods": self._get_supported_methods,
            "is_required": self._is_required,
            "register": self._registry.add,
            "unregister": self._registry.remove,
            "use": self._registry.use,
        }

    def handle_inf(self, body: dict[str, Any], client: Client) -> dict[str, Any]:
        """Handles an AUTH-INF message coming from the given client.

        Parameters:
            body: the body of the message
            client: the client that sent the message

        Returns:
            object: the body of the response of the message
        """
        return {"methods": self._registry.ids, "required": self._required}

    def handle_req(self, body: dict[str, Any], client: Client) -> dict[str, str]:
        """Handles an AUTH-REQ message coming from the given client.

        Parameters:
            body: the body of the message
            client: the client that sent the message

        Returns:
            object: the body of the response of the message
        """
        method = body.get("method")
        if client.user:
            response = AuthenticationResult.failure("Already authenticated")
        elif method and method in self._registry:
            method = self._registry.find_by_id(method)
            response = method.authenticate(client, body.get("data") or "")
        else:
            response = AuthenticationResult.failure(f"No such method: {method}")

        if response.successful:
            client.user = response.user

        return response.json

    def handle_whoami(self, body: dict[str, Any], client: Client) -> dict[str, str]:
        """Handles an AUTH-WHOAMI message coming from the given client.

        Parameters:
            body: the body of the message
            client: the client that sent the message

        Returns:
            object: the body of the response of the message
        """
        return {"user": str(client.user or "")}

    async def run(self, app: "SkybrushServer") -> None:
        handler_map = {
            "AUTH-INF": self.handle_inf,
            "AUTH-REQ": self.handle_req,
            "AUTH-WHOAMI": self.handle_whoami,
        }
        types = sorted(handler_map.keys())

        async for body, sender, responder in app.message_hub.iterate(*types):
            handler = handler_map[body["type"]]
            try:
                response = handler(body, sender)
            except Exception as ex:
                response = {"type": "ACK-NAK", "reason": str(ex)}
            responder(response)

    def _get_supported_methods(self) -> list[str]:
        """Returns the list of supported authentication methods."""
        return sorted(self._registry.ids)

    def _is_required(self) -> bool:
        """Getter function that returns whether authentication is required
        on this server.
        """
        return self._required


construct = AuthenticationExtension
description = (
    "Authentication-related message handlers and authentication method registry"
)
schema = {
    "properties": {
        "required": {
            "type": "boolean",
            "title": "Require authentication",
            "description": "Tick this checkbox to require users to authenticate with the server after connection",
            "format": "checkbox",
        }
    }
}
