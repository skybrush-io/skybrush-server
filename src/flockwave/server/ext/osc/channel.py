from typing import List

from oscpy.parser import format_message

from flockwave.channels.message import MessageChannel
from flockwave.connections.base import Connection

from .message import OSCMessage


class OSCEncoder:
    """Simple OSC encoder class."""

    def __init__(self, encoding: str = "utf-8", encoding_errors: str = "strict"):
        """Constructor."""
        self._encoding = encoding
        self._encoding_errors = encoding_errors

    def __call__(self, message: OSCMessage) -> bytes:
        encoded, _ = format_message(
            message.address, message.values, self._encoding, self._encoding_errors
        )
        return encoded


class OSCParser:
    """Simple OSC parser class."""

    def __init__(self, encoding: str = "utf-8", encoding_errors: str = "strict"):
        """Constructor."""
        self._encoding = encoding
        self._encoding_errors = encoding_errors

    def __call__(self, data: bytes) -> List[OSCMessage]:
        """Feeds the given bytes into the parser and returns any OSC messages
        parsed out of it.
        """
        # TODO(ntamas): we don't do any parsing yet, maybe in the future
        return []


def create_osc_channel(connection: Connection) -> MessageChannel[OSCMessage]:
    return MessageChannel(connection, parser=OSCParser(), encoder=OSCEncoder())
