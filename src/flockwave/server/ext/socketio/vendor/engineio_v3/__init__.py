"""Implementation of an Engine.IO server using the 3rd revision of the
Engine.IO protocol.
"""

from .trio_server import TrioServer

__all__ = ("TrioServer",)
