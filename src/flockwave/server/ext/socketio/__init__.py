"""Extension that extends the Skybrush server with support for incoming
messages on a Socket.IO connection.
"""

from .extension import dependencies, description, run, schema

__all__ = ("dependencies", "description", "run", "schema")
