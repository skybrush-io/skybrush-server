"""Logger object for the Skybrush gateway server."""

from flockwave.logger import log as base_log

__all__ = ("log",)

log = base_log.getChild("gateway")
