"""Implementation of an Engine.IO server using the 4th revision of the
Engine.IO protocol.
"""

from .trio_server import TrioServer

__all__ = ("TrioServer",)
__version__ = "4.3.0"  # this vendored library is based on python-engineio@4.3.0
