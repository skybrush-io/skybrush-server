"""Skybrush server extension that adds debugging tools and a test page to
the Skybrush server.
"""

from .extension import dependencies, load, index

__all__ = ("dependencies", "load", "index")
