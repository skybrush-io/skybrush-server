"""Implementation of a Socket.IO server using the 4th revision of the
Socket.IO protocol.

Socket.IO rev 4 is based on Engine.IO rev 3.
"""

from .trio_server import TrioServer

__all__ = ("TrioServer",)
