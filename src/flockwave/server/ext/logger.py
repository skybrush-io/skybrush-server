"""Logger object for the extension framework."""

from ..logger import log as base_log

__all__ = ("log",)

log = base_log.getChild("ext")
