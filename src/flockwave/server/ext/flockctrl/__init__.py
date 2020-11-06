"""Skybrush server extension that adds support for drone flocks that use
the ``flockctrl`` protocol.
"""

from .extension import construct, dependencies

__all__ = ("construct", "dependencies")
