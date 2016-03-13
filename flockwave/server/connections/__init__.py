"""Package that holds classes that implement connections to various
types of devices: serial ports, files, TCP sockets and so on.

Each connection class provided by this package has a common notion of a
*state*, which may be one of: disconnected, connecting, connected or
disconnecting. Connection instances send signals when their state changes.
"""

__all__ = ("Connection", "ConnectionBase", "ConnectionState",
           "reconnecting")

from .base import Connection, ConnectionBase, ConnectionState
from .reconnection import reconnecting
