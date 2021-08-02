"""Extension that implements an OSC client in Skybrush that forward the
positions of the drones to a remote OSC target.
"""

from .extension import description, run

__all__ = ("description", "run")
