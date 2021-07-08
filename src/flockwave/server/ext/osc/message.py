from dataclasses import dataclass
from typing import Tuple

from .types import OSCAddress, OSCValue

__all__ = ("OSCMessage",)


@dataclass(frozen=True)
class OSCMessage:
    """Simple data class representing an OSC message."""

    #: The OSC address where the message was (or will be) sent to
    address: OSCAddress

    #: The values in the OSC message
    values: Tuple[OSCValue, ...]
