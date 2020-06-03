"""Types commonly used throughout the MAVLink module."""

from typing import Any

__all__ = ("MAVLinkMessage",)


#: Type specification for messages parsed by the MAVLink parser. Unfortunately
#: we cannot refer to an exact Python class here because that depends on the
#: dialoect that we will be parsing
MAVLinkMessage = Any
