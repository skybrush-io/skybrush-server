"""Extension that extends the Skybrush server with an HTTP server listening
on a specific port.
"""

from .extension import description, exports, load, run, schema, unload
from .types import HTTPServerExtensionAPI

__all__ = (
    "description",
    "exports",
    "load",
    "run",
    "schema",
    "unload",
    "HTTPServerExtensionAPI",
)
