"""Types commonly used throughout the MAVLink module."""

from dataclasses import dataclass
from typing import Any

__all__ = ("MAVLinkMessage", "MAVLinkNetworkSpecification")


#: Type specification for messages parsed by the MAVLink parser. Unfortunately
#: we cannot refer to an exact Python class here because that depends on the
#: dialoect that we will be parsing
MAVLinkMessage = Any


@dataclass
class MAVLinkNetworkSpecification:
    """Parameter specification for a single MAVLink network."""

    #: Unique identifier of this MAVLink network that the server can use as a
    #: priamry key
    id: str

    #: MAVLink system ID reserved for the ground station (the Skybrush server)
    system_id: int = 255

    @classmethod
    def from_json(cls, obj) -> "MAVLinkNetworkSpecification":
        """Constructs a MAVLink network specification from its JSON
        representation.
        """
        result = cls(id=obj["id"])

        if "system_id" in obj:
            result.system_id = obj["system_id"]

        return result

    @property
    def json(self):
        """Returns the JSON representation of the network specification."""
        return {"id": self.id, "system_id": self.system_id}
