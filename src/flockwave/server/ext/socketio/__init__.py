"""Extension that extends the Flockwave server with support for incoming
messages on a Socket.IO connection.
"""

from .extension import dependencies, task

__all__ = ("dependencies", "task")
