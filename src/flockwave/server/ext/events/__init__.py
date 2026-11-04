"""Extension that allows clients to subscribe to server-side events by type and
receive `EVT-INF` notifications when these events occur.
"""

from .dispatcher import Dispatcher
from .extension import EventsExtensionAPI, construct, description, schema, tags
from .merging import MergingPolicy, keep_all, keep_first, keep_latest

__all__ = (
    "Dispatcher",
    "EventsExtensionAPI",
    "MergingPolicy",
    "construct",
    "description",
    "keep_all",
    "keep_first",
    "keep_latest",
    "schema",
    "tags",
)
