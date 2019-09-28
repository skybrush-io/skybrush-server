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
    TCPStreamConnection,
    UDPSocketConnection,
    MulticastUDPSocketConnection,
    SubnetBindingConnection,
    SubnetBindingUDPConnection,
    SubnetBindingUDPBroadcastConnection,
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
    "SubnetBindingConnection",
    "SubnetBindingUDPConnection",
    "SubnetBindingUDPBroadcastConnection",
    "SupervisionPolicy",
    "create_connection",
    "supervise",
)
