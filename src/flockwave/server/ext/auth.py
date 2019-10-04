"""Base extension that handles authentication-related messages and
keeps track of registered authentication methods.
"""

from flockwave.server.model.authentication import AuthenticationResult
from flockwave.server.model.client import Client
from flockwave.server.registries import AuthenticationMethodRegistry

from .base import UAVExtensionBase

from typing import Any, Dict


class AuthenticationExtension(UAVExtensionBase):
    """Extension that implements basic handling for authentication-related
    messages in the server.

    Note that this extension does not implement any particular authentication
    method; see other extensions starting with ``auth_`` for that.
    """

    def __init__(self):
        super().__init__()

        self._registry = AuthenticationMethodRegistry()
        self._required = False

    def configure(self, configuration):
        self._required = bool(configuration.get("required"))

    def exports(self) -> Dict[str, Any]:
        return {
            "register": self._registry.add,
            "unregister": self._registry.remove,
            "use": self._registry.use,
        }

    def handle_inf(self, body: Dict[str, Any], client: Client) -> Dict[str, Any]:
        """Handles an AUTH-INF message coming from the given client.

        Parameters:
            body: the body of the message
            client: the client that sent the message

        Returns:
            object: the body of the response of the message
        """
        return {"methods": self._registry.ids, "required": self._required}

    def handle_req(self, body: Dict[str, Any], client: Client) -> Dict[str, str]:
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
            response = method.authenticate(client, body.get("data"))
        else:
            response = AuthenticationResult.failure(f"No such method: {method}")

        if response.successful:
            client.user = response.user

        return response.json

    def handle_whoami(self, body: Dict[str, Any], client: Client) -> Dict[str, str]:
        """Handles an AUTH-WHOAMI message coming from the given client.

        Parameters:
            body: the body of the message
            client: the client that sent the message

        Returns:
            object: the body of the response of the message
        """
        return {"user": client.user or ""}

    async def run(self, app):
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


construct = AuthenticationExtension
