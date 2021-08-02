"""Extension that creates one or more virtual UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from .extension import construct, dependencies, description

__all__ = ("construct", "dependencies", "description")
