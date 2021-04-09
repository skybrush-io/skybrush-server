"""Extension that allows the Skybrush server to be discoverable on the
local network with UPnP/SSDP.
"""

from .extension import exports, load, run, unload

__all__ = ("exports", "load", "run", "unload")
