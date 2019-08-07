"""Package that holds classes that implement connections to various
types of devices: serial ports, files, TCP sockets and so on.

Each connection class provided by this package has a common notion of a
*state*, which may be one of: disconnected, connecting, connected or
disconnecting. Connection instances send signals when their state changes.
"""

from .base import Connection, ConnectionBase, ConnectionState
from .factory import ConnectionFactory, create_connection
from .file import FileConnection
from .serial import SerialPortConnection
from .socket import (
    TCPSocketConnection,
    UDPSocketConnection,
    MulticastUDPSocketConnection,
    SubnetBindingConnection,
    SubnetBindingUDPConnection,
    SubnetBindingUDPBroadcastConnection,
)
from .reconnection import reconnecting

__all__ = (
    "Connection",
    "ConnectionBase",
    "ConnectionFactory",
    "ConnectionState",
    "FileConnection",
    "SerialPortConnection",
    "TCPSocketConnection",
    "UDPSocketConnection",
    "MulticastUDPSocketConnection",
    "SubnetBindingConnection",
    "SubnetBindingUDPConnection",
    "SubnetBindingUDPBroadcastConnection",
    "create_connection",
    "reconnecting",
)
