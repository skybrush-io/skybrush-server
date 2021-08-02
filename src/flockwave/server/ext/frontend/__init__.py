"""Extension that adds a simple frontend index page to the Skybrush server,
served over HTTP.
"""

from .extension import dependencies, description, load

__all__ = ("dependencies", "description", "load")
