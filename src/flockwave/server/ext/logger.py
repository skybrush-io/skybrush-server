"""Logger object for the extension framework."""

from ..logger import add_id_to_log, log as base_log

__all__ = ("add_id_to_log", "log")

log = base_log.getChild("ext")
