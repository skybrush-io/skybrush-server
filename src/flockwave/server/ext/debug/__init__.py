"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

from .extension import dependencies, load, index

__all__ = ("dependencies", "load", "index")
