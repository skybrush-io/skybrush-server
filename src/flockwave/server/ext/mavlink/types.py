"""Types commonly used throughout the MAVLink module."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

__all__ = (
    "MAVLinkMessage",
    "MAVLinkMessageSpecification",
    "MAVLinkNetworkSpecification",
    "spec",
)


#: Type specification for messages parsed by the MAVLink parser. Unfortunately
#: we cannot refer to an exact Python class here because that depends on the
#: dialoect that we will be parsing
MAVLinkMessage = Any

#:
MAVLinkMessageSpecification = Tuple[str, Dict[str, Any]]


def _spec(name, **kwds):
    return (name, kwds)


class _MAVLinkMessageSpecificationFactory:
    """Convenience constructor for MAVLink message specifications."""

    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        name = name.upper()
        func = self._cache.get(name)
        if not func:
            self._cache[name] = func = lambda **kwds: (name, kwds)
        return func


spec = _MAVLinkMessageSpecificationFactory()


@dataclass
class MAVLinkNetworkSpecification:
    """Parameter specification for a single MAVLink network."""

    #: Unique identifier of this MAVLink network that the server can use as a
    #: priamry key
    id: str

    #: MAVLink system ID reserved for the ground station (the Skybrush server)
    system_id: int = 255

    #: Python format string that receives the MAVLink system ID of a drone
    #: and the network ID, and returns its preferred formatted identifier that
    #: is used when the drone is registered in the server
    id_format: str = "{1}{0}"

    #: The connections that the MAVLink network will consist of. A MAVLink
    #: network may have one or more connections where MAVLink messages are
    #: received and sent, but a system ID appearing on one of the networks
    #: identifies the _same_ device as the same system ID on another network.
    #: In most cases, one link is the primary one and the rest are used as
    #: "backups".
    #:
    #: The dictionary may contain any values that are accepted by th
    #: `create_connection()` function.
    connections: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj, id: Optional[str] = None) -> "MAVLinkNetworkSpecification":
        """Constructs a MAVLink network specification from its JSON
        representation.
        """
        result = cls(id=id if id is not None else obj["id"])

        if "system_id" in obj:
            result.system_id = obj["system_id"]

        if "id_format" in obj:
            result.id_format = obj["id_format"]

        if "connections" in obj:
            result.connections = obj["connections"]

        return result

    @property
    def json(self):
        """Returns the JSON representation of the network specification."""
        return {
            "id": self.id,
            "id_format": self.id_format,
            "system_id": self.system_id,
            "connections": self.connections,
        }
