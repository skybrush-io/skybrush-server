"""Model classes related to a single user that is connected to the server
via a client connection.
"""

import attr

__all__ = ("User",)


@attr.s(frozen=True)
class User:
    """A single user connected to the Flockwave server via a client
    connection.

    Attributes:
        name: the name of the user
        domain: the domain of the user; this allows multiple users to have
            the same user name as long as they belong to different
            authentication domains. Useful when integrating with third-party
            authentication systems such as Windows Active Directory
            domains.
    """

    name: str = attr.ib()
    domain: str = attr.ib(default="")
