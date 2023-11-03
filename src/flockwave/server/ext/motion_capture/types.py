__all__ = ("Attitude", "Position")

Position = tuple[float, float, float]
"""Type alias for 3D position data."""

Attitude = tuple[float, float, float, float]
"""Type alias for attitude data, expressed as a quaternion in Hamilton
conventions; i.e., the order of items is ``(w, x, y, z)``.
"""
