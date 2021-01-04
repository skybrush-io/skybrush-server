"""Skybrush server extension that adds debugging tools and a test page to
the Skybrush server.
"""

from .extension import dependencies, run, index

__all__ = ("dependencies", "run", "index")
