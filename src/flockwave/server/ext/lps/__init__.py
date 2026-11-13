"""Extension that provides some basic facilities for local positioning systems."""

from .extension import construct, description, schema, tags
from .types import LocalPositioningSystemsExtensionAPI

__all__ = (
    "LocalPositioningSystemsExtensionAPI",
    "construct",
    "description",
    "schema",
    "tags",
)
