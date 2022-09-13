from typing import Tuple

__all__ = ("Attitude", "Position")

Position = Tuple[float, float, float]
"""Type alias for 3D position data."""

Attitude = Tuple[float, float, float, float]
"""Type alias for attitude data, expressed as a quaternion in Hamilton
conventions; i.e., the order of items is ``(w, x, y, z)``.
"""
