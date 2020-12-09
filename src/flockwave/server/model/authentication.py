from abc import ABCMeta, abstractmethod, abstractproperty
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .client import Client


class AuthenticationResultType(Enum):
    FAILURE = "failure"
    SUCCESS = "success"
    CHALLENGE = "challenge"


@dataclass
class AuthenticationResult:
    type: AuthenticationResultType
    data: Optional[str] = None
    reason: Optional[str] = None
    user: Optional[str] = None

    @classmethod
    def challenge(cls, data):
        return cls(type=AuthenticationResultType.CHALLENGE, data=data)

    @classmethod
    def success(cls, user):
        return cls(type=AuthenticationResultType.SUCCESS, user=user)

    @classmethod
    def failure(cls, reason=None):
        return cls(type=AuthenticationResultType.FAILURE, reason=reason)

    @property
    def json(self):
        """Converts the response into its JSON representation."""
        if self.type is AuthenticationResultType.SUCCESS:
            if self.user is None:
                raise ValueError("successful authentication responses need a username")
            result = {"result": True, "user": str(self.user)}
        elif self.type is AuthenticationResultType.FAILURE:
            result = {"result": False, "reason": self.reason or "Authentication failed"}
        else:
            if self.data is None:
                raise ValueError("authentication challenges need a data member")
            result = {"data": str(self.data)}
        result["type"] = "AUTH-RESP"
        return result

    @property
    def successful(self):
        """Returns whether the response represents a successful authentication
        attempt.
        """
        return self.type is AuthenticationResultType.SUCCESS and self.user is not None


class AuthenticationMethod(metaclass=ABCMeta):
    """Interface specification for authentication methods."""

    @abstractproperty
    def id(self):
        """Identifier of the authentication method with which it will be
        registered in the server.
        """
        raise NotImplementedError

    @abstractmethod
    def authenticate(self, client: Client, data: str) -> None:
        """Handles an authentication request from a client.

        Parameters:
            client: the client that sent the authentication request
            data: the data that was sent in this request

        Returns:
            AuthenticationResult: response to the authentication request
        """
        raise NotImplementedError

    def cancel(self, client: Client) -> None:
        """Cancels the current authentication session of the given client.

        This method is relevant only for multi-step authentication methods.

        Parameters:
            client: the client whose authentication attempt is cancelled
        """
        pass
