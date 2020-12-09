"""Model classes related to a single user that is connected to the server
via a client connection.
"""

from dataclasses import dataclass
from flockwave.spec.ids import parse_user

__all__ = ("User",)


@dataclass(frozen=True)
class User:
    """A single user connected to the Skybrush server via a client
    connection.

    Attributes:
        name: the name of the user
        domain: the domain of the user; this allows multiple users to have
            the same user name as long as they belong to different
            authentication domains. Useful when integrating with third-party
            authentication systems such as Windows Active Directory
            domains.
    """

    name: str
    domain: str = ""

    @classmethod
    def from_string(cls, value):
        name, domain = parse_user(value)
        return cls(name=name, domain=domain)

    @property
    def is_logged_in(self) -> bool:
        """Returns whether this object represents a logged-in user."""
        return self.name or self.domain

    @property
    def json(self) -> str:
        return str(self)

    def __str__(self):
        return f"{self.name}@{self.domain}" if self.domain else self.name
