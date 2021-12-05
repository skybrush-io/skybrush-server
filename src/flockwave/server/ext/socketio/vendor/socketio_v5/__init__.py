"""Implementation of a Socket.IO server using the 5th revision of the
Socket.IO protocol.

Socket.IO rev 4 is based on Engine.IO rev 4.
"""

from .trio_server import TrioServer

__all__ = ("TrioServer",)
__version__ = "5.5.0"  # this vendored library is based on python-socketio@5.5.0
