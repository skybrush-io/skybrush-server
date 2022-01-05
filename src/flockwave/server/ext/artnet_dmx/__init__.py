"""Experimental, proof-of-concept ArtNet gateway that forwards ArtNet DMX
messages to a drone swarm based on a mapping from channels to drones.
"""

from .extension import description, run

__all__ = ("description", "run")

private = True  # pynsist: remove
