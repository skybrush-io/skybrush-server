"""Extension that allows the Skybrush server to be discoverable on the
local network with UPnP/SSDP.
"""

from .extension import description, exports, get_schema, load, run, unload

__all__ = ("description", "exports", "get_schema", "load", "run", "unload")
