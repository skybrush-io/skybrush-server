"""Package that holds classes that implement connections to various
types of devices: serial ports, files, TCP sockets and so on.

Each connection class provided by this package has a common notion of a
*state*, which may be one of: disconnected, connecting, connected or
disconnecting. Connection instances send signals when their state changes.
"""

from .base import Connection, ConnectionBase, ConnectionState
from .factory import ConnectionFactory, create_connection, create_connection_factory
from .file import FileConnection
from .serial import SerialPortConnection
from .socket import (
    TCPStreamConnection,
    UDPSocketConnection,
    MulticastUDPSocketConnection,
    BroadcastUDPSocketConnection,
)
from .stream import StreamConnection, StreamConnectionBase
from .supervision import (
    ConnectionSupervisor,
    ConnectionTask,
    SupervisionPolicy,
    supervise,
)

__all__ = (
    "Connection",
    "ConnectionBase",
    "ConnectionFactory",
    "ConnectionSupervisor",
    "ConnectionState",
    "ConnectionTask",
    "FileConnection",
    "SerialPortConnection",
    "StreamConnection",
    "StreamConnectionBase",
    "TCPStreamConnection",
    "UDPSocketConnection",
    "MulticastUDPSocketConnection",
    "BroadcastUDPSocketConnection",
    "SupervisionPolicy",
    "create_connection",
    "create_connection_factory",
    "supervise",
)
